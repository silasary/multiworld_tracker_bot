import asyncio
import datetime
import json
import logging
import os

import cattrs
import requests
from interactions import Client, Extension, SlashContext, listen
from interactions.client.errors import Forbidden
from interactions.ext.paginators import Paginator
from interactions.models.discord import Button, ButtonStyle, User
from interactions.models.internal.application_commands import (
    OptionType, integration_types, slash_command, slash_option)
from interactions.models.internal.tasks import IntervalTrigger, Task

from . import zoggoth
from .multiworld import (Datapackage, ItemClassification, Multiworld,
                         TrackedGame)

converter = cattrs.Converter()
converter.register_structure_hook(datetime.datetime, lambda x, *_: datetime.datetime.fromisoformat(x) if x else None)
converter.register_unstructure_hook(datetime.datetime, lambda x, *_: x.isoformat() if x else None)


class APTracker(Extension):
    def __init__(self, bot: Client) -> None:
        self.bot: Client = bot
        self.trackers: dict[int, list[TrackedGame]] = {}
        self.cheese: dict[str, Multiworld] = {}
        self.datapackages: dict[str, Datapackage] = {}
        self.load()
        zoggoth.update_all(self.datapackages)

    @listen()
    async def on_startup(self) -> None:
        self.refresh_all.start()
        self.refresh_all.trigger.last_call_time = datetime.datetime.now() - datetime.timedelta(hours=1)
        zoggoth.update_datapackage.start()
        # await zoggoth.update_datapackage()

    @slash_command("ap")
    @integration_types(guild=True, user=True)
    async def ap(self, ctx: SlashContext) -> None:
        """Monitor for Archipelago games"""

    @ap.subcommand("track")
    @slash_option("url", "URL of the multiworld tracker", OptionType.STRING, required=True)
    async def ap_track(self, ctx: SlashContext, url: str) -> None:
        """Track an Archipelago game."""
        if url.split("/")[-1].isnumeric():
            # Track slot
            await ctx.defer(ephemeral=True)
            if ctx.author_id not in self.trackers:
                self.trackers[ctx.author_id] = []

            for t in self.trackers[ctx.author_id]:
                if t.url == url:
                    tracker = t
                    break
            else:
                tracker = TrackedGame(url)
                self.trackers[ctx.author_id].append(tracker)
                self.save()

            room, multiworld = await self.url_to_multiworld('/'.join(url.split("/")[:-2]))
            await multiworld.refresh()
            slot = multiworld.games[int(url.split("/")[-1])]
            tracker.game = slot["game"]
            self.check_for_dp(tracker)

            tracker.name = f"{room} - **{slot['name']}**"
            await self.ap_refresh(ctx)
        else:
            # Track cheese room
            await ctx.defer(ephemeral=True)
            await self.sync_cheese(ctx.author, url)
            await self.ap_refresh(ctx)

    def check_for_dp(self, tracker):
        if tracker.game is None:
            return

        if tracker.game not in self.datapackages:
            self.datapackages[tracker.game] = Datapackage(items={})
            zoggoth.load_datapackage(tracker.game, self.datapackages[tracker.game])

    @ap.subcommand("refresh")
    async def ap_refresh(self, ctx: SlashContext) -> None:
        if ctx.author_id not in self.trackers:
            await ctx.send(f"Track a game with {self.ap_track.mention()} first", ephemeral=True)
            return

        if ctx.guild_id:
            await ctx.defer(ephemeral=True)
        else:
            await ctx.defer()

        games = {}
        for tracker in self.trackers[ctx.author_id]:
            new_items = tracker.refresh()
            if new_items:
                games[tracker] = new_items

        if not games:
            await ctx.send("No new items", ephemeral=True)
            return

        for tracker, items in games.items():
            await self.send_new_items(ctx, tracker, items)

        for tracker, items in games.items():
            await self.try_classify(ctx, tracker, items)

    async def try_classify(self, ctx: SlashContext | User, tracker: TrackedGame, new_items: list[str]) -> None:
        unclassified = [i[0] for i in new_items if self.get_classification(tracker.game, i[0]) == ItemClassification.unknown]
        for item in unclassified:
            filler = Button(style=ButtonStyle.GREY, label="Filler")
            useful = Button(style=ButtonStyle.GREEN, label="Useful")
            progression = Button(style=ButtonStyle.BLUE, label="Progression")
            msg = await ctx.send(
                f"[{tracker.game}] What kind of item is {item}?",
                ephemeral=False,
                components=[[filler, useful, progression]],
            )
            try:
                chosen = await self.bot.wait_for_component(msg, timeout=3600)
                if chosen.ctx.custom_id == filler.custom_id:
                    classification = ItemClassification.filler
                elif chosen.ctx.custom_id == useful.custom_id:
                    classification = ItemClassification.useful
                elif chosen.ctx.custom_id == progression.custom_id:
                    classification = ItemClassification.progression
                else:
                    print(f"wat: {chosen.ctx.custom_id}")
                self.datapackages[tracker.game].items[item] = classification
                await chosen.ctx.send(f"✅{item} is {classification}", ephemeral=True)
            except TimeoutError:
                await msg.channel.delete_message(msg)
                return
            await msg.channel.delete_message(msg)
            self.save()
        zoggoth.load_datapackage(tracker.game, self.datapackages[tracker.game])

    async def send_new_items(
        self,
        ctx_or_user: SlashContext | User,
        tracker: TrackedGame,
        new_items: list[list[str]],
    ):
        def icon(item):
            if tracker.game in self.datapackages and item in self.datapackages[tracker.game].items:
                classification = self.datapackages[tracker.game].items[item]
                if classification == ItemClassification.filler:
                    return "<:apfiller:1277502385459171338>"
                if classification == ItemClassification.useful:
                    return "<:apuseful:1277502389729103913>"
                if classification == ItemClassification.progression:
                    return "<:approg:1277502382682542143>"
                if classification == ItemClassification.trap:
                    return "❌"
            return "❓"

        names = [f"{icon(i[0])} {i[0]}" for i in new_items]
        slot_name = tracker.name or tracker.url

        if len(names) == 1:
            await ctx_or_user.send(f"{slot_name}: {names[0]}", ephemeral=False)
        elif len(names) > 10:
            text = f"{slot_name}:\n{', '.join(names)}"
            if len(text) > 1900:
                paginator = Paginator.create_from_string(self.bot, text)
                await paginator.send(ctx_or_user)
            else:
                await ctx_or_user.send(text, ephemeral=False)
        else:
            await ctx_or_user.send(f"{slot_name}: {', '.join(names)}", ephemeral=False)

    # @ap.subcommand("cheese")
    @slash_option("room", "room-id", OptionType.STRING, required=True)
    async def ap_cheese(self, ctx: SlashContext, room: str) -> None:
        await ctx.defer()
        await self.sync_cheese(ctx.author, room)
        await self.ap_refresh(ctx)

    async def sync_cheese(self, player: User, room: str) -> Multiworld:
        room, multiworld = await self.url_to_multiworld(room)

        await multiworld.refresh()
        self.cheese[room] = multiworld
        age = datetime.datetime.now(tz=datetime.timezone.utc) - multiworld.last_update

        for game in multiworld.games.values():
            game["url"] = f'https://archipelago.gg/tracker/{room}/0/{game["position"]}'
            is_game_done = game["checks_done"] == game["checks_total"]
            is_game_abandoned = multiworld.last_activity() < datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=21)

            for t in self.trackers.get(player.id, []):
                if t.url == game["url"]:
                    tracker = t
                    break
                elif t.url == game["url"].replace('/tracker/', '/generic_tracker/'):
                    tracker = t
                    break
            else:
                tracker = None


            if game.get("effective_discord_username") == player.username:
                if player.id not in self.trackers:
                    self.trackers[player.id] = []

                if tracker is None:
                    if is_game_done or age > datetime.timedelta(days=1) or is_game_abandoned:
                        continue

                    tracker = TrackedGame(game["url"])
                    self.trackers[player.id].append(tracker)
                    self.save()
                    tracker.game = game["game"]
                    self.check_for_dp(tracker)

            if tracker:
                if multiworld.title:
                    tracker.name = f"***{multiworld.title}*** - **{game['name']}**"
                else:
                    tracker.name = f"{room} - **{game['name']}**"
                tracker.game = game["game"]

                if is_game_done:
                    await player.send(f"Game {tracker.name} is complete")
                    self.remove_tracker(player, game["url"])
                    continue

                if is_game_abandoned:
                    # await player.send(f"Game {tracker.name} has finished")
                    self.remove_tracker(player, game["url"])
                    continue

        return multiworld

    async def url_to_multiworld(self, room):
        if 'cheesetrackers' in room:
            ch_id = room.split('/')[-1]
            multiworld = Multiworld(f"https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/{ch_id}")
            await multiworld.refresh()
            room = multiworld.upstream_url

        if '/tracker/' in room:
            room = room.split('/')[-1]
        multiworld = self.cheese.get(room)
        if multiworld is None:
            ap_url = f"https://archipelago.gg/tracker/{room}"
            ch_id = (
                requests.post(
                    "https://cheesetrackers.theincrediblewheelofchee.se/api/tracker",
                    json={"url": ap_url},
                )
                .json()
                .get("tracker_id")
            )
            multiworld = Multiworld(f"https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/{ch_id}")
        return room,multiworld

    def remove_tracker(self, player, url):
        for t in self.trackers[player.id]:
            if t.url == url:
                self.trackers[player.id].remove(t)
                return

    @Task.create(IntervalTrigger(hours=1))
    async def refresh_all(self) -> None:
        for user, trackers in self.trackers.copy().items():
            player = await self.bot.fetch_user(user)
            if not player:
                continue
            urls = set()
            for tracker in trackers:
                if tracker.url in urls:
                    self.remove_tracker(player, tracker.url)
                    self.save()
                    continue
                urls.add(tracker.url)
                await self.sync_cheese(player, tracker.tracker_id)
                new_items = tracker.refresh()
                if not new_items and tracker.failures > 10:
                    self.remove_tracker(player, tracker.url)
                    await player.send(f"Tracker {tracker.url} has been removed due to errors")
                    continue

                try:
                    if new_items:
                        await self.send_new_items(player, tracker, new_items)
                        asyncio.create_task(self.try_classify(player, tracker, new_items))
                except Forbidden:
                    logging.error(f"Failed to send message to {player.id}")
                    tracker.failures += 1

                await asyncio.sleep(30)

        to_delete = []
        for room_id, multiwold in self.cheese.items():
            if multiwold.last_update and datetime.datetime.now(tz=multiwold.last_update.tzinfo) - multiwold.last_update > datetime.timedelta(days=7):
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)
            elif multiwold.last_update is None and datetime.datetime.now() - multiwold.last_check > datetime.timedelta(days=30):
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)

        for room_id in to_delete:
            del self.cheese[room_id]

        self.save()

    def get_classification(self, game, item):
        if game not in self.datapackages:
            self.datapackages[game] = Datapackage(items={})
            zoggoth.load_datapackage(game, self.datapackages[game])
        if item not in self.datapackages[game].items:
            self.datapackages[game].items[item] = ItemClassification.unknown
        return self.datapackages[game].items[item]

    def save(self):
        trackers = json.dumps(converter.unstructure(self.trackers), indent=2)
        with open("trackers.json", "w") as f:
            f.write(trackers)
        cheese = json.dumps(converter.unstructure(self.cheese), indent=2)
        with open("cheese.json", "w") as f:
            f.write(cheese)
        dp = json.dumps(converter.unstructure(self.datapackages), indent=2)
        with open("datapackages.json", "w") as f:
            f.write(dp)

    def load(self):
        if os.path.exists("trackers.json"):
            with open("trackers.json") as f:
                self.trackers = converter.structure(json.loads(f.read()), dict[int, list[TrackedGame]])
        try:
            if os.path.exists("cheese.json"):
                with open("cheese.json") as f:
                    self.cheese = converter.structure(json.loads(f.read()), dict[str, Multiworld])
        except Exception as e:
            print(e)
        try:
            if os.path.exists("datapackages.json"):
                with open("datapackages.json") as f:
                    self.datapackages = converter.structure(json.loads(f.read()), dict[str, Datapackage])
        except Exception as e:
            print(e)


def recolour_buttons(components: list[Button]) -> list[Button]:
    buttons = []
    if not components:
        return []
    for c in components[0].components:
        if isinstance(c, Button):
            buttons.append(Button(style=ButtonStyle.GREY, label=c.label, emoji=c.emoji, disabled=True))
    return buttons
