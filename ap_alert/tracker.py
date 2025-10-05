import asyncio
from collections import Counter, defaultdict
import datetime
import json
import logging
import os
import re
import itertools

import sentry_sdk
from interactions import (
    ActionRow,
    Activity,
    ActivityType,
    Client,
    ComponentContext,
    Extension,
    InteractionContext,
    SlashContext,
    component_callback,
    listen,
    Timestamp,
    TimestampStyles,
    spread_to_rows,
)
from interactions.client.errors import Forbidden, NotFound
from interactions.ext.paginators import Paginator
from interactions.models.discord import Button, ButtonStyle, User, Embed, Message, ContainerComponent, TextDisplayComponent
from interactions.models.internal.application_commands import OptionType, integration_types, slash_command, slash_option
from interactions.models.internal.tasks import IntervalTrigger, Task
from requests.structures import CaseInsensitiveDict

from .models.enums import CompletionStatus, ProgressionStatus

from .models.player import Player
from ap_alert.converter import converter
from shared.exceptions import BadAPIKeyException

from . import external_data
from .multiworld import (
    GAMES,
    Datapackage,
    Filters,
    HintFilters,
    ItemClassification,
    Multiworld,
    NetworkItem,
    TrackedGame,
)
from .worlds import TRACKERS

regex_dash = re.compile(r"dash:(-?\d+)")
regex_unblock = re.compile(r"unblock:(\d+)")
regex_remove = re.compile(r"remove:(-?\d+)")
regex_disable = re.compile(r"disable:(-?\d+)")
regex_bk = re.compile(r"bk:(\d+)")
regex_inv = re.compile(r"inv:(-?\d+)")
regex_settings = re.compile(r"settings:(-?\d+)")
regex_filter = re.compile(r"filter:(\d+|default):(-?\d+)")
regex_hint_filter = re.compile(r"hint_filter:(\d+|default):(-?\d+)")


class APTracker(Extension):
    stats: dict[str, int] = {}

    def __init__(self, bot: Client) -> None:
        self.bot: Client = bot
        self.trackers: dict[int, list[TrackedGame]] = {}
        self.cheese: dict[str, Multiworld] = CaseInsensitiveDict()
        self.datapackages: dict[str, Datapackage] = CaseInsensitiveDict()
        self.players: dict[int, Player] = {}
        self.load()

    def get_player_settings(self, id: int) -> Player:
        player = self.players.get(id)
        if player is None:
            player = Player(id)
            self.players[id] = player
        return player

    def get_trackers(self, id: int) -> list[TrackedGame]:
        return self.trackers.setdefault(id, [])

    @property
    def user_count(self):
        return self.stats.get("user_count", 0)

    @user_count.setter
    def user_count(self, value):
        self.stats["user_count"] = value
        self.save()

    @property
    def tracker_count(self):
        return self.stats.get("tracker_count", 0)

    @tracker_count.setter
    def tracker_count(self, value):
        self.stats["tracker_count"] = value
        self.save()

    @listen()
    async def on_startup(self) -> None:
        await external_data.load_all(self.datapackages)
        for _user, trackers in self.trackers.items():
            for tracker in trackers:
                await self.check_for_dp(tracker)
        self.refresh_all.start()
        self.refresh_all.trigger.last_call_time = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)
        external_data.update_datapackage.start()
        await external_data.update_datapackage()
        activity = Activity(name=f"{self.tracker_count} slots across {self.user_count} users", type=ActivityType.WATCHING)
        await self.bot.change_presence(activity=activity)

    @listen()
    async def on_disconnect(self) -> None:
        self.save()

    @slash_command("ap")
    @integration_types(guild=True, user=True)
    async def ap(self, ctx: SlashContext) -> None:
        """Monitor for Archipelago games"""

    @ap.subcommand("track")
    @slash_option("url", "URL of the multiworld tracker", OptionType.STRING, required=True)
    async def ap_track(self, ctx: SlashContext, url: str) -> None:
        """Track an Archipelago game."""
        if "AAAAAAAAAAAAAA" in url:
            await ctx.send("AAAAAAAAAAAAAA is an example room.  Please use the url for your async instead.", ephemeral=True)
            return

        ephemeral = await defer_ephemeral_if_guild(ctx)

        try:
            await ctx.author.fetch_dm()  # Make sure we can send DMs to this player
        except Forbidden:
            await ctx.send("I can't send you DMs, please enable them so I can notify you when you get new items.", ephemeral=True)
            return

        if "/room/" in url:
            await ctx.send("Please use the tracker URL, not the room URL", ephemeral=True)
            return
        if re.match(r"^archipelago.gg:\d+$", url):
            await ctx.send("Please use the tracker URL, not the port number", ephemeral=True)
            return

        if url.split("/")[-1].isnumeric():
            # Track slot
            for t in self.get_trackers(ctx.author_id):
                if t.url == url:
                    tracker = t
                    tracker.disabled = False
                    break
            else:
                tracker = TrackedGame(url)
                self.get_trackers(ctx.author_id).append(tracker)
                self.save()

            room, multiworld = await self.url_to_multiworld("/".join(url.split("/")[:-2]))
            if multiworld is None:
                await ctx.send(f"An error has occurred.  Could not set up `{room}` with {url}", ephemeral=True)
                return
            await ctx.send(f"Setting up tracker for https://{multiworld.ap_hostname}/tracker/{room}...", ephemeral=ephemeral)
            await multiworld.refresh()
            slot = multiworld.games[int(url.split("/")[-1])]
            tracker.game = slot["game"]
            await self.check_for_dp(tracker)

            tracker.name = f"{room} - **{slot['name']}**"
            await self.ap_refresh(ctx)
        else:
            # Track cheese room
            mw, found = await self.sync_cheese(ctx.author, url)
            if not mw:
                await ctx.send("Room not found", ephemeral=True)
                return
            if not found:
                await ctx.send("This is a multiworld tracker, please click provide the slot tracker URL by clicking the number next to your slot", ephemeral=True)
                return
            await self.ap_refresh(ctx)

    async def check_for_dp(self, tracker):
        if tracker.game is None:
            return

        if tracker.game not in self.datapackages or not self.datapackages[tracker.game].items:
            self.datapackages[tracker.game] = Datapackage(items={})
            await external_data.import_datapackage(tracker.game, self.datapackages[tracker.game])

    @ap.subcommand("refresh")
    async def ap_refresh(self, ctx: SlashContext) -> None:
        if not self.get_trackers(ctx.author_id):
            await ctx.send(f"Track a game with {self.ap_track.mention()} first", ephemeral=True)
            return

        ephemeral = await defer_ephemeral_if_guild(ctx)

        games = {}
        for tracker in self.get_trackers(ctx.author_id).copy():
            _room, multiworld = await self.url_to_multiworld(tracker.multitracker_url)
            new_items = await multiworld.refresh_game(tracker)
            if new_items:
                games[tracker] = tracker.notification_queue.copy()
            if tracker.failures >= 3:
                self.remove_tracker(ctx.author, tracker)
                await ctx.send(f"Tracker {tracker.url} has been removed due to errors", ephemeral=ephemeral)
                self.save()

        if not games:
            await ctx.send("No new items", ephemeral=True)
            self.save()
            return

        for tracker, items in games.items():
            await self.send_new_items(ctx, tracker, ephemeral=ephemeral)

        for tracker, items in games.items():
            await self.try_classify(ctx, tracker, items, ephemeral=ephemeral)
        self.save()

    @ap.subcommand("authenticate")
    @slash_option("api_key", "Your Cheese Tracker API key", OptionType.STRING, required=True)
    async def ap_authenticate(self, ctx: SlashContext, api_key: str) -> None:
        """Authenticate with Cheese Tracker. This allows the bot to automatically track your claimed games."""
        player = self.get_player_settings(ctx.author_id)
        player.cheese_api_key = api_key.strip()
        await ctx.send("API key saved", ephemeral=True)
        self.save()
        try:
            cheese_dash = await player.get_trackers()
        except BadAPIKeyException:
            await ctx.send("That's not a valid API Key...  Please copy it directly from https://cheesetrackers.theincrediblewheelofchee.se/settings", ephemeral=True)
            player.cheese_api_key = None
            self.save()
            return

        for multiworld in cheese_dash:
            await self.sync_cheese(ctx.author, multiworld)

    async def try_classify(self, ctx: SlashContext | User, tracker: TrackedGame, new_items: list[NetworkItem], ephemeral: bool = False) -> None:
        if tracker.game is None:
            return
        unclassified = [i.name for i in new_items if i.classification in [ItemClassification.unknown, ItemClassification.bad_name]]
        n = 0
        for item in unclassified:
            if TRACKERS.get(tracker.game) and (classification := TRACKERS[tracker.game].classify(tracker, item)):
                if self.datapackages[tracker.game].set_classification(item, classification):
                    continue

            trap = Button(style=ButtonStyle.RED, label="Trap", emoji="❌")
            filler = Button(style=ButtonStyle.GREY, label="Filler", emoji="<:filler:1277502385459171338>")
            useful = Button(style=ButtonStyle.GREEN, label="Useful", emoji="<:useful:1277502389729103913>")
            progression = Button(style=ButtonStyle.BLUE, label="Progression", emoji="<:progression:1277502382682542143>")
            mcguffin = Button(style=ButtonStyle.BLUE, label="McGuffin", emoji="✨")
            skip = Button(style=ButtonStyle.GREY, label="Skip", emoji="⏭️")
            msg = await ctx.send(
                f"[{tracker.game}] What kind of item is {item}?",
                ephemeral=ephemeral,
                components=spread_to_rows(trap, filler, useful, progression, mcguffin, skip),
            )
            try:
                chosen = await self.bot.wait_for_component(msg, timeout=3600)
                if chosen is None:
                    classification = ItemClassification.unknown
                elif chosen.ctx.custom_id == filler.custom_id:
                    classification = ItemClassification.filler
                elif chosen.ctx.custom_id == useful.custom_id:
                    classification = ItemClassification.useful
                elif chosen.ctx.custom_id == progression.custom_id:
                    classification = ItemClassification.progression
                elif chosen.ctx.custom_id == trap.custom_id:
                    classification = ItemClassification.trap
                elif chosen.ctx.custom_id == mcguffin.custom_id:
                    classification = ItemClassification.mcguffin
                elif chosen.ctx.custom_id == skip.custom_id:
                    classification = ItemClassification.unknown
                else:
                    print(f"wat: {chosen.ctx.custom_id}")
                if tracker.game not in self.datapackages:
                    self.datapackages[tracker.game] = Datapackage(items={})
                self.datapackages[tracker.game].set_classification(item, classification)
                await chosen.ctx.send(f"✅{item} is {classification}", ephemeral=True)
                n += 1
                if n > 3 and isinstance(ctx, InteractionContext):
                    ctx = ctx.author
                    ephemeral = False

            except TimeoutError:
                if not ephemeral:
                    await msg.channel.delete_message(msg)
                break
            try:
                if not ephemeral:
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
        *,
        ephemeral: bool = False,
        inventory: bool = False,
    ) -> Message | None:
        async def icon(item: NetworkItem) -> str:
            emoji = "❓"

            classification = item.classification
            if classification == ItemClassification.unknown and tracker.game in self.datapackages:
                classification = self.datapackages[tracker.game].items.setdefault(item.name, ItemClassification.unknown)
            if inventory and classification == ItemClassification.unknown:
                await self.try_classify(ctx_or_user, tracker, new_items)
                classification = self.datapackages[tracker.game].items[item.name]
            if classification == ItemClassification.mcguffin:
                emoji = "✨"
            if classification == ItemClassification.filler:
                emoji = "<:filler:1277502385459171338>"
            if classification == ItemClassification.useful:
                emoji = "<:useful:1277502389729103913>"
            if classification == ItemClassification.progression:
                emoji = "<:progression:1277502382682542143>"
            if classification == ItemClassification.trap:
                emoji = "❌"

            if inventory or item.quantity > 1:
                return f"{emoji} {item.name} x{item.quantity}"
            return f"{emoji} {item.name}"

        if inventory:
            new_items = list(NetworkItem(i, tracker.game, tracker.all_items[i]) for i in tracker.all_items)
        else:
            new_items = tracker.notification_queue.copy()

        tracker.notification_queue.clear()

        names = [await icon(i) for i in new_items]
        slot_name = tracker.name or tracker.url

        if len(names) == 1:
            components = []
            if tracker.filters == Filters.unset:
                components.append(Button(style=ButtonStyle.GREY, label="Configure Filters", emoji="⚙️", custom_id=f"settings:{tracker.id}"))
            await ctx_or_user.send(f"{slot_name}: {names[0]}", ephemeral=ephemeral, components=components)
        elif len(names) > 10:
            text = f"{slot_name}:\n"
            classes: dict[ItemClassification, list[NetworkItem]] = defaultdict(list)
            classes.update(
                {  # presort the keys
                    ItemClassification.mcguffin: [],
                    ItemClassification.progression: [],
                    ItemClassification.unknown: [],
                    ItemClassification.useful: [],
                    ItemClassification.filler: [],
                    ItemClassification.trap: [],
                }
            )

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
        return None

    @ap.subcommand("dashboard")
    async def ap_dashboard(self, ctx: SlashContext) -> None:
        await ctx.defer(ephemeral=True)
        if not self.get_trackers(ctx.author_id):
            await ctx.send(f"Track a game with {self.ap_track.mention()} first", ephemeral=True)
            return

        trackers = self.get_trackers(ctx.author_id)
        if not trackers:
            await ctx.send("No games tracked", ephemeral=True)
            return

        buttons: list[Button] = []
        for tracker in trackers:
            if tracker.disabled:
                continue
            if tracker.name is None:
                tracker.name = f"{tracker.tracker_id} #{tracker.slot_id}"
            name = tracker.name.replace("*", "")
            if len(name) > 80:
                name = f"{name[:70]} #{tracker.slot_id}"
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
        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return Embed(title="Game not found")

        multiworld = self.cheese.get(tracker.tracker_id)

        name = tracker.name
        if multiworld:
            port = f" ({multiworld.last_port})" if multiworld.last_port else ""
            if not name.endswith(port):
                name = name + port

        dp = self.datapackages.get(tracker.game)

        last_refreshed = format_relative_time(tracker.last_refresh) or "Never"
        last_item_name = f"{dp.icon(tracker.last_item[0])} {tracker.last_item[0]}" if tracker.last_item[0] else ""
        last_item_time = format_relative_time(tracker.last_item[1]) if tracker.last_item[0] else "N/A"
        prog_name = f"{dp.icon(tracker.last_progression[0])} {tracker.last_progression[0]}" if tracker.last_progression[0] else ""
        prog_time = format_relative_time(tracker.last_progression[1]) if tracker.last_progression[0] else "N/A"
        check_time = max(tracker.last_checked, tracker.last_activity)
        last_activity = format_relative_time(check_time)

        embed = Embed(title=name)
        embed.set_author(tracker.game)
        embed.add_field("Last Refreshed", last_refreshed)
        embed.add_field("Last Item Received", last_item_name + " " + last_item_time)
        embed.add_field("Last Progression Item", prog_name + " " + prog_time)
        embed.add_field("Progression Status", f"{tracker.progression_status.name} (Last Checked: {last_activity})")
        components = []

        components.append(Button(style=ButtonStyle.GREY, label="Inventory", emoji="💼", custom_id=f"inv:{tracker.id}"))
        components.append(Button(style=ButtonStyle.GREY, label="Settings", emoji="⚙️", custom_id=f"settings:{tracker.id}"))
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
        else:
            components.append(Button(style=ButtonStyle.GREY, label="Remove", emoji="🗑️", custom_id=f"disable:{tracker.id}"))
        embeds = [embed]
        if TRACKERS.get(tracker.game) and (dash := await TRACKERS[tracker.game].build_dashboard(tracker)):
            embeds.append(dash)
        return await ctx.send(embeds=embeds, components=spread_to_rows(*components), ephemeral=True)

    @component_callback(regex_remove)
    async def remove(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_remove.match(ctx.custom_id)
        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        self.get_trackers(ctx.author_id).remove(tracker)
        await ctx.send("Tracker removed", ephemeral=True)

    @component_callback(regex_disable)
    async def disable(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_disable.match(ctx.custom_id)
        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        tracker.disabled = True
        await ctx.send("Tracker removed", ephemeral=True)

    @component_callback(regex_unblock)
    async def unblock(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_unblock.match(ctx.custom_id)
        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        multiworld = self.cheese[tracker.tracker_id]
        await multiworld.refresh(force=True)
        game = multiworld.games[tracker.slot_id]
        game["progression_status"] = ProgressionStatus.unblocked
        # game['last_checked'] = datetime.datetime.now(tz=datetime.timezone.utc)
        await multiworld.put(game)
        tracker.progression_status = ProgressionStatus.unblocked

        await ctx.send("Progression status updated", ephemeral=True)

    @component_callback(regex_bk)
    async def still_bk(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_bk.match(ctx.custom_id)
        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        multiworld = self.cheese[tracker.tracker_id]
        await multiworld.refresh(force=True)
        game = multiworld.games[tracker.slot_id]
        # game["progression_status"] = ProgressionStatus.bk
        game["last_checked"] = datetime.datetime.now(tz=datetime.timezone.utc)
        await multiworld.put(game)

        await ctx.send("Progression status updated", ephemeral=True)

    @component_callback(regex_inv)
    async def inventory(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_inv.match(ctx.custom_id)
        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        if not tracker.all_items:
            _room, multiworld = await self.url_to_multiworld(tracker.multitracker_url)
            await multiworld.refresh_game(tracker)
        await self.send_new_items(ctx, tracker, ephemeral=True, inventory=True)

    @component_callback(regex_settings)
    async def settings(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True, edit_origin=False)
        m = regex_settings.match(ctx.custom_id)
        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        multiworld = self.cheese[tracker.tracker_id]

        name = tracker.name
        port = f" ({multiworld.last_port})" if multiworld.last_port else ""
        if not name.endswith(port):
            name = name + port

        def filter_button(name: str, value: Filters):
            colour = ButtonStyle.GREY
            if value == tracker.filters:
                colour = ButtonStyle.GREEN
            return Button(style=colour, label=name, custom_id=f"filter:{tracker.id}:{value.value}")

        def hint_filter_button(name: str, value: HintFilters):
            colour = ButtonStyle.GREY
            if value == tracker.hint_filters:
                colour = ButtonStyle.GREEN
            return Button(style=colour, label=name, custom_id=f"hint_filter:{tracker.id}:{value.value}")

        components = [
            TextDisplayComponent(f"# {name}"),
            ContainerComponent(
                TextDisplayComponent("## Item Filters"),
                ActionRow(
                    filter_button("Filter: Nothing", Filters.none),
                    filter_button("Filter: Everything", Filters.everything),
                    filter_button("Filter: Useful+", Filters.useful_plus),
                ),
                ActionRow(
                    filter_button("Filter: Useful+Progression", Filters.useful_plus_progression),
                    filter_button("Filter: Progression", Filters.progression),
                    filter_button("Filter: Prog+McGuffins", Filters.progression_plus),
                ),
            ),
            ContainerComponent(
                TextDisplayComponent("## Hint Filters"),
                ActionRow(
                    hint_filter_button("Hint Filter: Nothing", HintFilters.none),
                    hint_filter_button("Hint Filter: Everything", HintFilters.all),
                    hint_filter_button("Hint Filter: Received", HintFilters.finder),
                    hint_filter_button("Hint Filter: Sent", HintFilters.receiver),
                ),
            ),
        ]

        await ctx.send(components=components, ephemeral=True)

    @component_callback(regex_filter)
    async def filter(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_filter.match(ctx.custom_id)
        if m.group(1) == "default":
            player_settings = self.get_player_settings(ctx.author_id)
            player_settings.default_filters = Filters(int(m.group(2)))
            await ctx.send("Default filter updated", ephemeral=True)
            return

        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        tracker.filters = Filters(int(m.group(2)))
        await ctx.send("Filter updated", ephemeral=True)

    @component_callback(regex_hint_filter)
    async def hint_filter(self, ctx: ComponentContext) -> None:
        await ctx.defer(ephemeral=True)
        m = regex_hint_filter.match(ctx.custom_id)
        if m.group(1) == "default":
            player_settings = self.get_player_settings(ctx.author_id)
            player_settings.default_hint_filters = HintFilters(int(m.group(2)))
            await ctx.send("Default hint filter updated", ephemeral=True)
            return

        tracker = next((t for t in self.get_trackers(ctx.author_id) if t.id == int(m.group(1))), None)
        if tracker is None:
            return
        tracker.hint_filters = HintFilters(int(m.group(2)))
        await ctx.send("Hint filter updated", ephemeral=True)

    @ap.subcommand("settings")
    async def ap_settings(self, ctx: SlashContext) -> None:
        """Configure your Archipelago settings."""
        player_settings = self.get_player_settings(ctx.author_id)

        def filter_button(name: str, value: Filters):
            colour = ButtonStyle.GREY
            if value == player_settings.default_filters:
                colour = ButtonStyle.GREEN
            return Button(style=colour, label=name, custom_id=f"filter:default:{value.value}")

        def hint_filter_button(name: str, value: HintFilters):
            colour = ButtonStyle.GREY
            if value == player_settings.default_hint_filters:
                colour = ButtonStyle.GREEN
            return Button(style=colour, label=name, custom_id=f"hint_filter:default:{value.value}")

        components = [
            TextDisplayComponent("# Player Settings"),
            ContainerComponent(
                TextDisplayComponent("## Item Filters"),
                ActionRow(
                    filter_button("Filter: Nothing", Filters.none),
                    filter_button("Filter: Everything", Filters.everything),
                    filter_button("Filter: Useful+", Filters.useful_plus),
                ),
                ActionRow(
                    filter_button("Filter: Useful+Progression", Filters.useful_plus_progression),
                    filter_button("Filter: Progression", Filters.progression),
                    filter_button("Filter: Prog+McGuffins", Filters.progression_plus),
                    filter_button("Filter: No Default", Filters.unset),
                ),
            ),
            ContainerComponent(
                TextDisplayComponent("## Hint Filters"),
                ActionRow(
                    hint_filter_button("Hint Filter: Nothing", HintFilters.none),
                    hint_filter_button("Hint Filter: Everything", HintFilters.all),
                    hint_filter_button("Hint Filter: Received", HintFilters.finder),
                    hint_filter_button("Hint Filter: Sent", HintFilters.receiver),
                    hint_filter_button("Hint Filter: No Default", HintFilters.unset),
                ),
            ),
        ]
        await ctx.send(components=components, ephemeral=True)

    async def sync_cheese(self, player: User, room: str | Multiworld) -> tuple[Multiworld, bool]:
        room, multiworld = await self.url_to_multiworld(room)
        if multiworld is None:
            return None, False

        found_tracker = False

        await multiworld.refresh()
        self.cheese[room] = multiworld
        age = datetime.datetime.now(tz=datetime.timezone.utc) - multiworld.last_update
        is_mw_abandoned = multiworld.last_activity() < datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=30)

        for game in multiworld.games.values():
            game["url"] = f'{multiworld.ap_scheme}://{multiworld.ap_hostname}/tracker/{room}/0/{game["position"]}'

            for t in self.get_trackers(player.id):
                if t.url == game["url"]:
                    tracker = t
                    break
                elif t.url == game["url"].replace("/tracker/", "/generic_tracker/"):
                    tracker = t
                    break
            else:
                tracker = None

            if game.get("effective_discord_username") == player.username:
                if tracker is None:
                    is_game_done = game["checks_done"] == game["checks_total"] or game.completion_status in [CompletionStatus.done, CompletionStatus.released]
                    # If either condition is true, we don't want to autotrack track this game.
                    if is_game_done or age > datetime.timedelta(days=1) or is_mw_abandoned or multiworld.goaled:
                        continue

                    tracker = TrackedGame(game["url"])
                    self.get_trackers(player.id).append(tracker)
                    tracker.game = game["game"]
                    await self.check_for_dp(tracker)

            if tracker:
                if multiworld.title:
                    tracker.name = f"***{multiworld.title}*** - **{game['name']}**"
                else:
                    tracker.name = f"{room} - **{game['name']}**"
                tracker.update(game)

                if game["checks_done"] == game["checks_total"] and game.completion_status in [CompletionStatus.done, CompletionStatus.released]:
                    # Removing needs an and, because 100% no goal can happen.
                    self.remove_tracker(player, tracker.url)
                    await player.send(f"Game {tracker.name} is complete")
                    continue

                if is_mw_abandoned:
                    last_check = format_relative_time(multiworld.last_activity())
                    self.remove_tracker(player, tracker.url)
                    await player.send(f"Game {tracker.name} has stalled, the last check in the multiworld was {last_check}. Removing tracker.")
                    continue
                elif multiworld.goaled:
                    self.remove_tracker(player, tracker.url)
                    await player.send(f"{multiworld.title} is complete, removing {tracker.name}")
                    continue
                found_tracker = True

        return multiworld, found_tracker

    async def url_to_multiworld(self, room: str) -> tuple[str, Multiworld]:
        if isinstance(room, Multiworld):
            multiworld = room
            if multiworld.upstream_url is None:
                await multiworld.refresh()
            room = multiworld.upstream_url.split("/")[-1]
            return room, multiworld

        if "cheesetrackers" in room:
            ch_id = room.split("/")[-1]
            multiworld = Multiworld(f"https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/{ch_id}")
            await multiworld.refresh()
            room = multiworld.upstream_url

        ap_url = None
        if "/tracker/" in room or "/generic_tracker/" in room:
            ap_url = room
            room = room.split("/")[-1]

        multiworld = self.cheese.get(room)
        if multiworld is None:
            if ap_url is None:
                ap_url = f"https://archipelago.gg/tracker/{room}"
            if "generic_tracker" in ap_url:
                ap_url = ap_url.replace("generic_tracker", "tracker")

            multiworld = Multiworld(ap_url)
            if not multiworld.title:
                multiworld.title = room

        return room, multiworld

    def remove_tracker(self, player, tracker: str | TrackedGame) -> None:
        for t in self.get_trackers(player.id).copy():
            if isinstance(tracker, str) and t.url == tracker:
                self.get_trackers(player.id).remove(t)
                return
            elif isinstance(tracker, TrackedGame) and t == tracker:
                self.get_trackers(player.id).remove(t)
                return

    @Task.create(IntervalTrigger(hours=1))
    async def refresh_all(self) -> None:
        user_count = 0
        tracker_count = 0
        progress = 0
        games: dict[str, int] = {}

        for user, trackers in self.trackers.copy().items():
            try:
                player = await self.bot.fetch_user(user)
                if not player:
                    continue

                player_settings = self.get_player_settings(player.id)
                player_settings.update(player)

                if player_settings.cheese_api_key:
                    try:
                        cheese_dash = await player_settings.get_trackers()
                        for multiworld in cheese_dash:
                            await self.sync_cheese(player, multiworld)
                    except BadAPIKeyException:
                        player_settings.cheese_api_key = None
                        self.save()
                        await player.send("Failed to authenticate with Cheese Tracker.  Please reauthenticate with `/ap authenticate`")
                cheese_dash = []

                urls = set()
                ids = set()
                for tracker in trackers:
                    try:
                        if tracker.failures >= 10:
                            self.remove_tracker(player, tracker)
                            await player.send(f"Tracker {tracker.url} has been removed due to errors")
                            continue

                        if tracker.url in urls:
                            self.remove_tracker(player, tracker)
                            continue
                        if tracker.id in ids:
                            self.remove_tracker(player, tracker)
                            self.save()
                            continue
                        urls.add(tracker.url)
                        if tracker.id:
                            ids.add(tracker.id)
                        try:
                            multiworld, _found = await self.sync_cheese(player, tracker.multitracker_url)
                        except IndexError:
                            tracker.failures += 1
                            continue
                        if multiworld is None:
                            tracker.failures += 1
                            if tracker.failures >= 3:
                                self.remove_tracker(player, tracker.url)
                                await player.send(f"Tracker {tracker.url} has been removed due to errors")
                            continue

                        if tracker.filters == Filters.unset and player_settings.default_filters != Filters.unset:
                            tracker.filters = player_settings.default_filters
                        if tracker.hint_filters == HintFilters.unset and player_settings.default_hint_filters != HintFilters.unset:
                            tracker.hint_filters = player_settings.default_hint_filters

                        should_check = (
                            tracker.last_refresh is None
                            or tracker.last_refresh.tzinfo is None
                            or multiworld.last_activity() > tracker.last_refresh
                            or datetime.datetime.now(tz=datetime.UTC) - tracker.last_checked > datetime.timedelta(hours=3)
                        )
                        if tracker.disabled:
                            should_check = False

                        if should_check:
                            new_items = await multiworld.refresh_game(tracker)
                        else:
                            new_items = False

                        ### DEBUG
                        if not player_settings.quiet_mode:
                            try:
                                if not new_items and tracker.failures > 10:
                                    self.remove_tracker(player, tracker.url)
                                    await player.send(f"Tracker {tracker.url} has been removed due to errors")
                                    continue
                                if new_items:
                                    items = tracker.notification_queue.copy()
                                    await self.send_new_items(player, tracker)
                                    asyncio.create_task(self.try_classify(player, tracker, items))
                            except Forbidden:
                                logging.error(f"Failed to send message to {player.global_name} ({player.id})")
                                tracker.failures += 1
                                continue

                            hints = []
                            try:
                                if not tracker.disabled:
                                    hints = tracker.refresh_hints(multiworld)
                            except Exception as e:
                                sentry_sdk.capture_exception(e)
                                logging.error(f"Failed to get hints for {tracker.name}")
                            try:
                                if hints:
                                    components = []
                                    if tracker.hint_filters == HintFilters.unset:
                                        components.append(Button(style=ButtonStyle.GREY, label="Configure Hint Filters", emoji="⚙️", custom_id=f"settings:{tracker.id}"))
                                    await player.send(f"New hints for {tracker.name}:", embeds=[h.embed() for h in hints], components=components)
                            except Forbidden:
                                logging.error(f"Failed to send message to {player.global_name} ({player.id})")
                                tracker.failures += 1
                                continue

                        tracker_count += 1
                        progress += 1
                        games[tracker.game] = games.get(tracker.game, 0) + 1
                        if should_check:
                            if "webtracker" in multiworld.agents:
                                await asyncio.sleep(3)  # Webtrackers are slow
                            else:
                                await asyncio.sleep(1)
                        else:
                            # if we didn't check anything, we don't need to wait
                            await asyncio.sleep(0)
                    except Exception as e:
                        logging.error(f"Error occurred while processing tracker {tracker.id} for user {user}: {e}")
                        sentry_sdk.capture_exception(e)

                if trackers:
                    user_count += 1
                if progress > 100:
                    self.save()
                    progress = 0
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logging.error(f"Failed to refresh trackers for {user}")
                print(e)
                await asyncio.sleep(5)

        agents = Counter()
        to_delete = []
        for room_id, multiworld in self.cheese.items():
            if multiworld.last_update and datetime.datetime.now(tz=multiworld.last_update.tzinfo) - multiworld.last_update > datetime.timedelta(days=7):
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)
            elif not multiworld.last_update and not multiworld.last_refreshed:
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)
            elif datetime.datetime.now(tz=multiworld.last_refreshed.tzinfo) - multiworld.last_refreshed > datetime.timedelta(days=30):
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)
            elif multiworld.last_activity() < datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=30):
                logging.info(f"Removing {room_id} from cheese trackers")
                to_delete.append(room_id)

            for agent in multiworld.agents:
                if multiworld.agents[agent].enabled:
                    agents[agent] += 1

        for room_id in to_delete:
            del self.cheese[room_id]

        self.tracker_count = tracker_count
        self.user_count = user_count
        self.stats["games"] = games
        self.stats["agents"] = dict(agents)
        self.save()
        activity = Activity(name=f"{tracker_count} slots across {user_count} users", type=ActivityType.WATCHING)
        await self.bot.change_presence(activity=activity)

    async def get_classification(self, game, item):
        if game not in self.datapackages:
            self.datapackages[game] = Datapackage(items={})
            await external_data.import_datapackage(game, self.datapackages[game])
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
        players = json.dumps(converter.unstructure(self.players), indent=2)
        with open("players.json", "w") as f:
            f.write(players)
        stats = json.dumps(self.stats, indent=2)
        with open("stats.json", "w") as f:
            f.write(stats)

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
                    self.datapackages.update(converter.structure(json.loads(f.read()), dict[str, Datapackage]))
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)
        try:
            if os.path.exists("players.json"):
                with open("players.json") as f:
                    self.players = converter.structure(json.loads(f.read()), dict[int, Player])
                for player in self.players.values():
                    if player.cheese_api_key:
                        self.get_trackers(player.id)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)
        try:
            if os.path.exists("datapackages.json"):
                os.unlink("datapackages.json")
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
                    self.stats = stats
        except Exception as e:
            sentry_sdk.capture_exception(e)
            print(e)


def recolour_buttons(components: list[ActionRow]) -> list[Button]:
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
