import asyncio
import datetime
import json
import logging
import os
import re
import itertools

import requests
import sentry_sdk
from interactions import Activity, ActivityType, Client, ComponentContext, Extension, SlashContext, component_callback, listen, Timestamp, TimestampStyles, spread_to_rows
from interactions.client.errors import Forbidden, NotFound
from interactions.ext.paginators import Paginator
from interactions.models.discord import Button, ButtonStyle, User, Embed, Message
from interactions.models.internal.application_commands import (
    OptionType, integration_types, slash_command, slash_option)
from interactions.models.internal.tasks import IntervalTrigger, Task

from ap_alert.converter import converter

from . import zoggoth
from .multiworld import (GAMES, Datapackage, Filters, HintFilters, ItemClassification, Multiworld, NetworkItem, OldDatapackage, ProgressionStatus, TrackedGame, CompletionStatus)


regex_dash = re.compile(r"dash:(\d+)")
regex_unblock = re.compile(r"unblock:(\d+)")
regex_remove = re.compile(r"remove:(\d+)")
regex_bk = re.compile(r"bk:(\d+)")
regex_inv = re.compile(r"inv:(\d+)")
regex_settings = re.compile(r"settings:(\d+)")
regex_filter = re.compile(r"filter:(\d+):(\d+)")
regex_hint_filter = re.compile(r"hint_filter:(\d+):(\d+)")

class APTracker(Extension):
    def __init__(self, bot: Client) -> None:
        self.bot: Client = bot
        self.trackers: dict[int, list[TrackedGame]] = {}
        self.cheese: dict[str, Multiworld] = {}
        self.datapackages: dict[str, Datapackage] = {}
        self.tracker_count = 0
        self.load()
        zoggoth.update_all(self.datapackages)

    @listen()
    async def on_startup(self) -> None:
        self.refresh_all.start()
        self.refresh_all.trigger.last_call_time = datetime.datetime.now() - datetime.timedelta(hours=1)
        zoggoth.update_datapackage.start()
        # await zoggoth.update_datapackage()
        activity = Activity(name=f"{self.tracker_count} slots across {self.user_count} users", type=ActivityType.WATCHING)
        await self.bot.change_presence(activity=activity)

    @slash_command("ap")
    @integration_types(guild=True, user=True)
    async def ap(self, ctx: SlashContext) -> None:
        """Monitor for Archipelago games"""

    @ap.subcommand("track")
    @slash_option("url", "URL of the multiworld tracker", OptionType.STRING, required=True)
    async def ap_track(self, ctx: SlashContext, url: str) -> None:
        """Track an Archipelago game."""
        try:
            await ctx.author.fetch_dm()  # Make sure we can send DMs to this player
        except Forbidden:
            await ctx.send("I can't send you DMs, please enable them so I can notify you when you get new items.", ephemeral=True)
            return

        if '/room/' in url:
            await ctx.send("Please use the tracker URL, not the room URL", ephemeral=True)
            return
        if re.match(r'^archipelago.gg:\d+$', url):
            await ctx.send("Please use the tracker URL, not the port number", ephemeral=True)
            return

        ephemeral = await defer_ephemeral_if_guild(ctx)

        if url.split("/")[-1].isnumeric():
            # Track slot
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
            await ctx.send(f"Setting up tracker for https://archipelago.gg/tracker/{room}...", ephemeral=ephemeral)
            await multiworld.refresh()
            slot = multiworld.games[int(url.split("/")[-1])]
            tracker.game = slot["game"]
            self.check_for_dp(tracker)

            tracker.name = f"{room} - **{slot['name']}**"
            await self.ap_refresh(ctx)
        else:
            # Track cheese room
            _mw, found = await self.sync_cheese(ctx.author, url)
            if not found:
                await ctx.send("This is a multiworld tracker, please click provide the slot tracker URL by clicking the number next to your slot", ephemeral=True)
                return
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

        ephemeral = await defer_ephemeral_if_guild(ctx)

        games = {}
        for tracker in self.trackers[ctx.author_id]:
            new_items = tracker.refresh()
            if new_items:
                games[tracker] = new_items

        if not games:
            await ctx.send("No new items", ephemeral=True)
            self.save()
            return

        for tracker, items in games.items():
            await self.send_new_items(ctx, tracker, items, ephemeral)

        for tracker, items in games.items():
            await self.try_classify(ctx, tracker, items, ephemeral)
        self.save()

    async def try_classify(self, ctx: SlashContext | User, tracker: TrackedGame, new_items: list[NetworkItem], ephemeral: bool = False) -> None:
        if tracker.game is None:
            return

        unclassified = [i.name for i in new_items if i.classification == ItemClassification.unknown]
        for item in unclassified:
            trap = Button(style=ButtonStyle.RED, label="Trap", emoji="❌")
            filler = Button(style=ButtonStyle.GREY, label="Filler", emoji="<:filler:1277502385459171338>")
            useful = Button(style=ButtonStyle.GREEN, label="Useful", emoji="<:useful:1277502389729103913>")
            progression = Button(style=ButtonStyle.BLUE, label="Progression", emoji="<:progression:1277502382682542143>")
            mcguffin = Button(style=ButtonStyle.BLUE, label="McGuffin", emoji="✨")
            msg = await ctx.send(
                f"[{tracker.game}] What kind of item is {item}?",
                ephemeral=ephemeral,
                components=[[trap, filler, useful, progression, mcguffin]],
            )
            try:
                chosen = await self.bot.wait_for_component(msg, timeout=3600)
                if chosen.ctx.custom_id == filler.custom_id:
                    classification = ItemClassification.filler
                elif chosen.ctx.custom_id == useful.custom_id:
                    classification = ItemClassification.useful
                elif chosen.ctx.custom_id == progression.custom_id:
                    classification = ItemClassification.progression
                elif chosen.ctx.custom_id == trap.custom_id:
                    classification = ItemClassification.trap
                elif chosen.ctx.custom_id == mcguffin.custom_id:
                    classification = ItemClassification.mcguffin
                else:
                    print(f"wat: {chosen.ctx.custom_id}")
                self.datapackages[tracker.game].items[item] = classification
                await chosen.ctx.send(f"✅{item} is {classification}", ephemeral=True)
            except TimeoutError:
                await msg.channel.delete_message(msg)
                break
            try:
                await msg.channel.delete_message(msg)
            except NotFound:
                pass
            except Forbidden:
                pass
            self.save()

    async def send_new_items(
        self,
        ctx_or_user: SlashContext | User,
        tracker: TrackedGame,
        new_items: list[NetworkItem],
        ephemeral: bool = False,
        inventory: bool = False,
    ) -> Message:
        async def icon(item: NetworkItem) -> str:
            emoji = "❓"

            if tracker.game in self.datapackages and item.name in self.datapackages[tracker.game].items:
                classification = self.datapackages[tracker.game].items[item.name]
                if inventory and classification == ItemClassification.unknown:
                    await self.try_classify(ctx_or_user, tracker, new_items)
                    classification = self.datapackages[tracker.game].items[item.name]
                if classification == ItemClassification.mcguffin:
                    emoji =  "✨"
                if classification == ItemClassification.filler:
                    emoji = f"<:{'f' if len(new_items) > 10 else 'filler'}:1277502385459171338>"
                if classification == ItemClassification.useful:
                    emoji = f"<:{'u' if len(new_items) > 10 else 'useful'}:1277502389729103913>"
                if classification == ItemClassification.progression:
                    emoji = f"<:{'p' if len(new_items) > 10 else 'progression'}:1277502382682542143>"
                if classification == ItemClassification.trap:
                    emoji = "❌"

            if inventory:
                return f'{emoji} {item.name} x{item.quantity}'
            return f'{emoji} {item.name}'

        names = [await icon(i) for i in new_items]
        slot_name = tracker.name or tracker.url

        if len(names) == 1:
            components = []
            if tracker.filters == Filters.unset:
                components.append(Button(style=ButtonStyle.GREY, label="Configure Filters",  emoji="⚙️", custom_id=f"settings:{tracker.id}"))
            await ctx_or_user.send(f"{slot_name}: {names[0]}", ephemeral=ephemeral, components=components)
        elif len(names) > 10:
            text = f"{slot_name}:\n"
            classes = {
                ItemClassification.mcguffin: [],
                ItemClassification.progression: [],
                ItemClassification.unknown: [],
                ItemClassification.useful: [],
                ItemClassification.filler: [],
                ItemClassification.trap: []
            }

            for item in new_items:
                classification = item.classification
                classes[classification].append(item)
            for classification, items in classes.items():
                if items:
                    text += f"## {classification.name}:\n"
                    text += "\n".join([await icon(i) for i in items]) + "\n"

            if len(text) > 1900:
                paginator = Paginator.create_from_string(self.bot, text)
                if isinstance(ctx_or_user, User):
                    # I hate this so much.  Paginators currently require a context, but we're sliding into DMs.
                    # This makes the user look like a context so that the paginator can do button things and not crash.
                    ctx_or_user.author = ctx_or_user
                return await paginator.send(ctx_or_user, ephemeral=ephemeral)
            else:
                return await ctx_or_user.send(text, ephemeral=ephemeral)
        else:
            return await ctx_or_user.send(f"{slot_name}: {', '.join(names)}", ephemeral=ephemeral)

    @ap.subcommand("dashboard")
    async def ap_dashboard(self, ctx: SlashContext) -> None:
        await ctx.defer(ephemeral=True)
        if ctx.author_id not in self.trackers:
            await ctx.send(f"Track a game with {self.ap_track.mention()} first", ephemeral=True)
            return

        trackers = self.trackers[ctx.author_id]
        if not trackers:
            await ctx.send("No games tracked", ephemeral=True)
            return

        buttons: list[Button] = []
        for tracker in trackers:
            name = tracker.name.replace("*", "")
            colour = ButtonStyle.BLUE
            if tracker.progression_status == ProgressionStatus.bk:
                colour = ButtonStyle.RED
            elif tracker.progression_status == ProgressionStatus.go or tracker.progression_status == ProgressionStatus.unblocked:
                colour = ButtonStyle.GREEN
            if tracker.id == -1:
                tracker.id = min(trackers, key=lambda x: x.id).id - 1

            buttons.append(Button(style=colour, label=name, custom_id=f"dash:{tracker.id}"))
        buttons.sort(key=lambda x: x.style)
        pages = chunk(buttons, 25)
        for page in pages:
            await ctx.send("Select a game to view", ephemeral=True, components=spread_to_rows(*page))

    @component_callback(regex_dash)
    async def dashboard_embed(self, ctx: ComponentContext) -> Embed:
        await ctx.defer(ephemeral=True)
        m = regex_dash.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return Embed(title="Game not found")

        multiworld = self.cheese.get(tracker.tracker_id)

        name = tracker.name
        if multiworld:
            port = f' ({multiworld.last_port})' if multiworld.last_port else ''
            if not name.endswith(port):
                name = name + port

        embed = Embed(title=name)
        last_check = format_relative_time(tracker.last_refresh) or "Never"
        embed.add_field("Last Refreshed", last_check)
        last_item = format_relative_time(tracker.last_item[1]) if tracker.last_item[0] else "N/A"
        embed.add_field("Last Item Received", tracker.last_item[0] + " " + last_item)
        prog_time = format_relative_time(tracker.last_progression[1]) if tracker.last_progression[0] else "N/A"
        embed.add_field("Last Progression Item", tracker.last_progression[0] + " " + prog_time)
        check_time = max(tracker.last_checked, tracker.last_activity)
        last_checked = format_relative_time(check_time)
        embed.add_field("Progression Status", f'{tracker.progression_status.name} (Last Checked: {last_checked})')
        components = []

        components.append(Button(style=ButtonStyle.GREY, label="Inventory", emoji="💼", custom_id=f"inv:{tracker.id}"))
        components.append(Button(style=ButtonStyle.GREY, label="Settings",  emoji="⚙️", custom_id=f"settings:{tracker.id}"))
        if multiworld and multiworld.room_link:
            components.append(Button(style=ButtonStyle.URL, label="Open Room", url=multiworld.room_link))

        if multiworld:
            is_owner = multiworld.games[tracker.slot_id].get("effective_discord_username") == ctx.author.username
            only_game = len([g for g in multiworld.games.values() if g.get("effective_discord_username") == ctx.author.username]) == 1
        else:
            is_owner = False
            only_game = True

        # aged = check_time < datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=1)
        if is_owner:
            if tracker.progression_status == ProgressionStatus.bk:
                components.append(Button(style=ButtonStyle.GREEN, label="Unblocked", custom_id=f"unblock:{tracker.id}"))
                components.append(Button(style=ButtonStyle.RED, label="Still BK", custom_id=f"bk:{tracker.id}"))
            elif tracker.progression_status == ProgressionStatus.soft_bk:
                components.append(Button(style=ButtonStyle.GREEN, label="Unblocked", custom_id=f"unblock:{tracker.id}"))
                components.append(Button(style=ButtonStyle.RED, label="Still Soft BK", custom_id=f"bk:{tracker.id}"))
            elif tracker.progression_status in [ProgressionStatus.unblocked, ProgressionStatus.unknown]:
                components.append(Button(style=ButtonStyle.GREEN, label="Unblocked", custom_id=f"unblock:{tracker.id}"))
                components.append(Button(style=ButtonStyle.RED, label="BK", custom_id=f"bk:{tracker.id}"))

        if not is_owner or only_game:
            components.append(Button(style=ButtonStyle.GREY, label="Remove", emoji="🗑️", custom_id=f"remove:{tracker.id}"))


        return await ctx.send(embed=embed, components=components, ephemeral=True)

    @component_callback(regex_remove)
    async def remove(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_remove.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        self.trackers[ctx.author_id].remove(tracker)
        await ctx.send("Tracker removed", ephemeral=True)
        self.save()

    @component_callback(regex_unblock)
    async def unblock(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_unblock.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        multiworld = self.cheese[tracker.tracker_id]
        await multiworld.refresh(force=True)
        game = multiworld.games[tracker.slot_id]
        game["progression_status"] = ProgressionStatus.unblocked
        # game['last_checked'] = datetime.datetime.now(tz=datetime.timezone.utc)
        multiworld.put(game)
        tracker.progression_status = ProgressionStatus.unblocked

        await ctx.send("Progression status updated", ephemeral=True)

    @component_callback(regex_bk)
    async def still_bk(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_bk.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        multiworld = self.cheese[tracker.tracker_id]
        await multiworld.refresh(force=True)
        game = multiworld.games[tracker.slot_id]
        # game["progression_status"] = ProgressionStatus.bk
        game['last_checked'] = datetime.datetime.now(tz=datetime.timezone.utc)
        multiworld.put(game)

        await ctx.send("Progression status updated", ephemeral=True)

    @component_callback(regex_inv)
    async def inventory(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_inv.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        if not tracker.all_items:
            tracker.refresh()
        await self.send_new_items(ctx, tracker, list(NetworkItem(i, tracker.game, tracker.all_items[i]) for i in tracker.all_items), ephemeral=True, inventory=True)

    @component_callback(regex_settings)
    async def settings(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True, edit_origin=False)
        m = regex_settings.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        multiworld = self.cheese[tracker.tracker_id]

        name = tracker.name
        port = f' ({multiworld.last_port})' if multiworld.last_port else ''
        if not name.endswith(port):
            name = name + port

        embed = Embed(title=name)
        embed.add_field("Current Notification Filter", tracker.filters.name)
        components = []
        def filter_button(name: str, value: Filters):
            colour = ButtonStyle.GREY
            if value == tracker.filters:
                colour = ButtonStyle.GREEN
            return Button(style=colour, label=name, custom_id=f"filter:{tracker.id}:{value.value}")
        def hint_filter_button(name: str, value: Filters):
            colour = ButtonStyle.GREY
            if value == tracker.hint_filters:
                colour = ButtonStyle.GREEN
            return Button(style=colour, label=name, custom_id=f"hint_filter:{tracker.id}:{value.value}")
        components.append(filter_button("Filter: Nothing", Filters.none))
        components.append(filter_button("Filter: Everything", Filters.everything))
        components.append(filter_button("Filter: Useful+", Filters.useful_plus))
        components.append(filter_button("Filter: Progression", Filters.progression))
        components.append(filter_button("Filter: Prog+McGuffins", Filters.progression_plus))
        ### Second row
        components.append(hint_filter_button("Hint Filter: Nothing", HintFilters.none))
        components.append(hint_filter_button("Hint Filter: Everything", HintFilters.all))
        components.append(hint_filter_button("Hint Filter: Received", HintFilters.finder))
        components.append(hint_filter_button("Hint Filter: Sent", HintFilters.receiver))

        await ctx.send(embed=embed, components=spread_to_rows(*components))

    @component_callback(regex_filter)
    async def filter(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_filter.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        tracker.filters = Filters(int(m.group(2)))
        await ctx.send("Filter updated", ephemeral=True)
        self.save()

    @component_callback(regex_hint_filter)
    async def hint_filter(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_hint_filter.match(ctx.custom_id)
        tracker = next((t for t in self.trackers[ctx.author_id] if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        tracker.hint_filters = HintFilters(int(m.group(2)))
        await ctx.send("Hint filter updated", ephemeral=True)
        self.save()

    async def sync_cheese(self, player: User, room: str) -> tuple[Multiworld, bool]:
        room, multiworld = await self.url_to_multiworld(room)
        found_tracker = False

        await multiworld.refresh()
        self.cheese[room] = multiworld
        age = datetime.datetime.now(tz=datetime.timezone.utc) - multiworld.last_update
        is_mw_abandoned = multiworld.last_activity() < datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=30)

        for game in multiworld.games.values():
            game["url"] = f'https://archipelago.gg/tracker/{room}/0/{game["position"]}'
            is_game_done = game["checks_done"] == game["checks_total"] and game.completion_status in [CompletionStatus.done, CompletionStatus.released]

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
                    if is_game_done or age > datetime.timedelta(days=1) or is_mw_abandoned:
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
                tracker.update(game)

                if is_game_done:
                    self.remove_tracker(player, tracker.url)
                    await player.send(f"Game {tracker.name} is complete")
                    continue

                if is_mw_abandoned:
                    last_check = format_relative_time(multiworld.last_activity())
                    await player.send(f"Game {tracker.name} has stalled, the last check in the multiworld was {last_check}. Removing tracker.")
                    self.remove_tracker(player, tracker.url)
                    continue
                found_tracker = True

        return multiworld, found_tracker

    async def url_to_multiworld(self, room):
        if 'cheesetrackers' in room:
            ch_id = room.split('/')[-1]
            multiworld = Multiworld(f"https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/{ch_id}")
            await multiworld.refresh()
            room = multiworld.upstream_url

        if '/tracker/' in room or '/generic_tracker/' in room:
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
            if not multiworld.title:
                multiworld.title = room
        return room, multiworld

    def remove_tracker(self, player, url):
        for t in self.trackers[player.id].copy():
            if t.url == url:
                self.trackers[player.id].remove(t)
                return

    @Task.create(IntervalTrigger(hours=1))
    async def refresh_all(self) -> None:
        user_count = 0
        tracker_count = 0
        progress = 0

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
                multiworld, _found = await self.sync_cheese(player, tracker.tracker_id)
                new_items = tracker.refresh()

                try:
                    if not new_items and tracker.failures > 10:
                        self.remove_tracker(player, tracker.url)
                        await player.send(f"Tracker {tracker.url} has been removed due to errors")
                        continue
                    if new_items:
                        await self.send_new_items(player, tracker, new_items)
                        asyncio.create_task(self.try_classify(player, tracker, new_items))
                except Forbidden:
                    logging.error(f"Failed to send message to {player.global_name} ({player.id})")
                    tracker.failures += 1
                    continue

                hints = []
                try:
                    hints = tracker.refresh_hints(multiworld)
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logging.error(f"Failed to get hints for {tracker.name}")
                try:
                    if hints:
                        components = []
                        if tracker.hint_filters == HintFilters.unset:
                            components.append(Button(style=ButtonStyle.GREY, label="Configure Hint Filters",  emoji="⚙️", custom_id=f"settings:{tracker.id}"))
                        await player.send(f"New hints for {tracker.name}:", embeds=[h.embed() for h in hints], components=components)
                except Forbidden:
                    logging.error(f"Failed to send message to {player.global_name} ({player.id})")
                    tracker.failures += 1
                    continue

                tracker_count += 1
                progress += 1
                if self.tracker_count > 720:
                    await asyncio.sleep(3) # three doesn't go into 3600 evenly, so overflows will be spread out
                else:
                    await asyncio.sleep(5)
            if trackers:
                user_count += 1
            if progress > 10:
                self.save()
                progress = 0

        to_delete = []
        for room_id, multiworld in self.cheese.items():
            if multiworld.last_update and datetime.datetime.now(tz=multiworld.last_update.tzinfo) - multiworld.last_update > datetime.timedelta(days=7):
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)
            elif not multiworld.last_update and not multiworld.last_refreshed:
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)
            elif datetime.datetime.now() - multiworld.last_refreshed > datetime.timedelta(days=30):
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)

        for room_id in to_delete:
            del self.cheese[room_id]

        self.tracker_count = tracker_count
        self.user_count = user_count
        self.save()
        activity = Activity(name=f"{tracker_count} slots across {user_count} users", type=ActivityType.WATCHING)
        await self.bot.change_presence(activity=activity)

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
        with open("gamedata.json", "w") as f:
            f.write(dp)
        with open("stats.json", "w") as f:
            f.write(json.dumps({"tracker_count": self.tracker_count, "user_count": self.user_count}, indent=2))

    def load(self):
        if os.path.exists("trackers.json"):
            with open("trackers.json") as f:
                self.trackers = converter.structure(json.loads(f.read()), dict[int, list[TrackedGame]])
        try:
            if os.path.exists("cheese.json"):
                with open("cheese.json") as f:
                    self.cheese = converter.structure(json.loads(f.read()), dict[str, Multiworld])
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)
        try:
            if os.path.exists("gamedata.json"):
                with open("gamedata.json") as f:
                    self.datapackages = converter.structure(json.loads(f.read()), dict[str, Datapackage])
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)
        try:
            if os.path.exists("datapackages.json"):
                with open("datapackages.json") as f:
                    olds = converter.structure(json.loads(f.read()), dict[str, OldDatapackage])
                    for name, old in olds.items():
                        if name not in self.datapackages:
                            self.datapackages[name] = Datapackage(items={})
                        for k, v in old.items.items():
                            self.datapackages[name].items[k] = ItemClassification[v.name]

        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)
        try:
            if os.path.exists("flush.json"):
                with open("flush.json") as f:
                    flush = json.loads(f.read())
                    for dp in flush:
                        self.datapackages[dp].items.clear()
                os.rename("flush.json", "flushed.json")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)
        try:
            for mw in self.cheese.values():
                GAMES.update({g.id: g for g in mw.games.values()})
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)
        try:
            if os.path.exists("stats.json"):
                with open("stats.json") as f:
                    stats = json.loads(f.read())
                    self.tracker_count = stats.get("tracker_count", 0)
                    self.user_count = stats.get("user_count", 0)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)

def recolour_buttons(components: list[Button]) -> list[Button]:
    buttons = []
    if not components:
        return []
    for c in components[0].components:
        if isinstance(c, Button):
            buttons.append(Button(style=ButtonStyle.GREY, label=c.label, emoji=c.emoji, disabled=True))
    return buttons

async def defer_ephemeral_if_guild(ctx) -> bool:
    if ctx.guild_id:
        await ctx.defer(ephemeral=True, suppress_error=True)
        return True
    else:
        await ctx.defer(suppress_error=True)
        return False

def chunk(arr_range, arr_size):
    arr_range = iter(arr_range)
    return iter(lambda: tuple(itertools.islice(arr_range, arr_size)), ())

def format_relative_time(dt):
    if dt is None or dt == datetime.datetime.min:
        return ""
    return Timestamp.fromdatetime(dt).format(TimestampStyles.RelativeTime)
