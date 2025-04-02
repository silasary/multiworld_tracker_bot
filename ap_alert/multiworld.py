import datetime
import enum
import json
import logging
from typing import TYPE_CHECKING, Optional

import aiohttp
import interactions
from collections import defaultdict

import attrs
import requests
from bs4 import BeautifulSoup, Tag

from shared.exceptions import BadAPIKeyException
from world_data.models import Datapackage, ItemClassification

from .converter import converter

if TYPE_CHECKING:
    from enum import StrEnum as CursedStrEnum
else:
    from shared.cursed_enum import CursedStrEnum

OldClassification = enum.Enum("OldClassification", "unknown trap filler useful progression mcguffin")
ProgressionStatus = CursedStrEnum("ProgressionStatus", "unknown bk go soft_bk unblocked")
HintClassification = CursedStrEnum("HintClassification", "unset critical progression qol trash unknown")
HintUpdate = CursedStrEnum("HintUpdate", "none new found classified useless")
TrackerStatus = CursedStrEnum("TrackerStatus", "unknown disconnected connected ready playing goal_completed")
CompletionStatus = CursedStrEnum("CompletionStatus", "unknown incomplete all_checks goal done released")


@attrs.define()
class Hint:
    id: int
    item: str
    location: str
    entrance: str
    found: bool
    classification: HintClassification
    finder_game_id: int
    receiver_game_id: int | None = attrs.field(default=None)
    item_link_name: str | None = None

    update: HintUpdate = attrs.field(default=HintUpdate.none, init=False)
    is_finder: bool = attrs.field(default=False)

    @property
    def useless(self) -> bool:
        game = GAMES.get(self.receiver_game_id)
        return game and game.tracker_status == TrackerStatus.goal_completed

    def embed(self) -> dict:
        if self.update == HintUpdate.new:
            title = "New Hint"
        elif self.update == HintUpdate.found:
            title = "Hint Found"
        elif self.update == HintUpdate.classified:
            title = "Hint Reclassified"
        elif self.update == HintUpdate.useless:
            title = "Hint no longer needed"
        else:
            title = "Hint"
        receiver = GAMES.get(self.receiver_game_id)
        finder = GAMES.get(self.finder_game_id)

        if receiver is None:
            receiver = CheeseGame({"name": self.item_link_name or "(Item Link)"})
        if finder is None:
            finder = CheeseGame()

        item = f"{DATAPACKAGES[receiver.game].icon(self.item)} {self.item}"
        if self.is_finder:
            description = f"***{receiver.name}***'s ***{item}*** is at ***{self.location}***"
        else:
            description = f"***{receiver.name}***'s ***{item}*** is at {finder.name}'s ***{self.location}***"

        if self.entrance and self.entrance != "Vanilla":
            description += f" ({self.entrance})"

        # if self.classification == HintClassification.critical:
        #     description = f"❗ {description}"
        # elif self.classification == HintClassification.trash:
        #     description = f"🗑️ {description}"
        # elif self.classification == HintClassification.useful:
        #     description = f"🙋 {description}"

        embed = {
            "title": title,
            "description": description,
            # "color": self.classification.color,
            # "footer": {"text": f"Hint ID: {self.id}"},
        }
        if self.classification != HintClassification.unknown:
            embed["fields"] = [{"name": "Classification", "value": self.classification.title()}]

        return embed


# class ItemClassification(enum.Flag):
#     unknown = 0
#     trap = 1
#     filler = 2
#     useful = 4
#     progression = 8
#     mcguffin = 16

#     bad_name = 256


class Filters(enum.Flag):
    none = 0
    trap = 1
    filler = 2
    useful = 4
    progression = 8
    mcguffin = 16
    unset = 32

    everything = trap | filler | useful | progression | mcguffin
    useful_plus = useful | progression | mcguffin
    progression_plus = progression | mcguffin


class HintFilters(enum.Flag):
    none = 0
    receiver = 1
    finder = 2
    unset = 4

    all = receiver | finder


@attrs.define()
class NetworkItem:
    name: str
    game: str
    quantity: int

    @property
    def classification(self) -> ItemClassification:
        return DATAPACKAGES[self.game].items.get(self.name, ItemClassification.unknown)


# @attrs.define()
# class Datapackage:
#     items: dict[str, ItemClassification] = attrs.field(factory=dict)
#     categories: dict[str, ItemClassification] = attrs.field(factory=dict)

#     def icon(self, item_name: str) -> str:
#         classification = self.items.get(item_name, ItemClassification.unknown)
#         emoji = "❓"
#         if classification == ItemClassification.mcguffin:
#             emoji = "✨"
#         if classification == ItemClassification.filler:
#             emoji = "<:filler:1277502385459171338>"
#         if classification == ItemClassification.useful:
#             emoji = "<:useful:1277502389729103913>"
#         if classification == ItemClassification.progression:
#             emoji = "<:progression:1277502382682542143>"
#         if classification == ItemClassification.trap:
#             emoji = "❌"
#         return emoji

#     def set_classification(self, item_name: str, classification: ItemClassification) -> None:
#         if classification == ItemClassification.unknown and self.items.get(item_name, ItemClassification.unknown) != ItemClassification.unknown:
#             # We don't want to set an item to unknown if it's already classified
#             return
#         self.items[item_name] = classification


class CheeseGame(dict):
    @property
    def id(self) -> int:
        return self.get("id", -1)

    @property
    def game(self) -> str:
        return self.get("game", None)

    @property
    def progression_status(self) -> str:
        return ProgressionStatus(self.get("progression_status", "unknown"))

    @property
    def tracker_status(self) -> str:
        return TrackerStatus(self.get("tracker_status", "unknown"))

    @property
    def completion_status(self) -> str:
        return CompletionStatus(self.get("completion_status", "unknown"))

    @property
    def last_activity(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.get("last_activity", None) or "1970-01-01T00:00:00Z")

    @property
    def last_checked(self) -> datetime.datetime:
        last_checked_string = self.get("last_checked", None)
        if isinstance(last_checked_string, datetime.datetime):
            return last_checked_string
        return datetime.datetime.fromisoformat(last_checked_string or "1970-01-01T00:00:00Z")

    @property
    def name(self) -> str:
        return self.get("name", self.get("position", "Unknown"))


@attrs.define()
class Player:
    id: int
    name: str = None

    cheese_api_key: str | None = None

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    def __str__(self) -> str:
        return f"{self.name}#{self.discriminator}"

    async def get_trackers(self) -> list["Multiworld"]:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.cheese_api_key}"} if self.cheese_api_key else {}
            async with session.get("https://cheesetrackers.theincrediblewheelofchee.se/api/dashboard/tracker", headers=headers) as response:
                if response.status == 401:
                    raise BadAPIKeyException("Invalid API key.")
                data = await response.json()
        value = []
        for tracker in data:
            url = f"https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/{tracker['tracker_id']}"
            if MULTIWORLDS.get(tracker["tracker_id"]) is not None:
                value.append(MULTIWORLDS[tracker["tracker_id"]])
            else:
                value.append(Multiworld(url))
        return value

    def update(self, user: interactions.User) -> None:
        self.name = user.global_name


@attrs.define()
class TrackedGame:
    url: str  # https://archipelago.gg/tracker/tracker_id/0/slot_id
    id: int = -1
    latest_item: int = -1

    name: str = None
    game: str = None
    last_refresh: datetime.datetime = None
    last_recieved: datetime.datetime = None
    failures: int = 0
    filters: Filters = Filters.unset
    hint_filters: HintFilters = HintFilters.unset

    last_progression: tuple[str, datetime.datetime] = attrs.field(factory=lambda: ("", datetime.datetime.fromisoformat("1970-01-01T00:00:00Z")))
    last_item: tuple[str, datetime.datetime] = attrs.field(factory=lambda: ("", datetime.datetime.fromisoformat("1970-01-01T00:00:00Z")))
    progression_status: ProgressionStatus = ProgressionStatus.unknown
    last_checked: datetime.datetime = attrs.field(factory=lambda: datetime.datetime.fromisoformat("1970-01-01T00:00:00Z"))
    last_activity: datetime.datetime = attrs.field(factory=lambda: datetime.datetime.fromisoformat("1970-01-01T00:00:00Z"))

    all_items: dict[str, int] = attrs.field(factory=dict, init=False, repr=False)
    new_items: list[NetworkItem] = attrs.field(factory=list, init=False)

    checks: dict[str, bool] = attrs.field(factory=dict, repr=False)

    # hints: list[Hint] = attrs.field(factory=list)
    finder_hints: dict[int, Hint] = attrs.field(factory=dict, repr=False)
    receiver_hints: dict[int, Hint] = attrs.field(factory=dict, repr=False)

    def __hash__(self) -> int:
        return hash(self.url)

    @property
    def tracker_id(self) -> str:
        """ID of the multiworld tracker."""
        return self.url.split("/")[-3]

    @property
    def slot_id(self) -> int:
        return int(self.url.split("/")[-1])

    async def refresh(self) -> list[NetworkItem]:
        if self.game is None:
            await self.refresh_metadata()
        logging.info(f"Refreshing {self.url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.url) as response:
                    if response.status != 200:
                        self.failures += 1
                        return []
                    html = await response.text()
        except aiohttp.InvalidUrlClientError:
            # This is a bad URL, don't try again
            self.failures = 100
            return []
        # html = requests.get(self.url).content
        soup = BeautifulSoup(html, features="html.parser")
        title = soup.find("title").string
        if title == "Page Not Found (404)":
            self.failures += 1
            return []
        recieved = soup.find(id="received-table")
        if recieved is None:
            if "/tracker/" in self.url:
                self.url = self.url.replace("/tracker/", "/generic_tracker/")
                return await self.refresh()
        # headers = [i.string for i in recieved.find_all("th")]
        # rows = [[try_int(i.string) for i in r.find_all("td")] for r in recieved.find_all("tr")[1:]]
        self.process_locations(soup.find(id="locations-table"))
        rows = process_table(recieved)
        self.last_refresh = datetime.datetime.now(tz=datetime.timezone.utc)
        if not rows:
            return []

        index_order = "Last Order Received"
        index_amount = "Amount"
        index_item = "Item"

        rows.sort(key=lambda r: r[index_order])
        if rows[-1][index_order] < self.latest_item:
            self.latest_item = -1
            return [("Rollback detected!",)]
        is_up_to_date = rows[-1][index_order] == self.latest_item
        if is_up_to_date and self.all_items:
            return []

        new_items: list[NetworkItem] = []
        for r in rows:
            self.all_items[r[index_item]] = r[index_amount]
            if r[index_order] > self.latest_item:
                item = NetworkItem(r[index_item], self.game, r[index_amount])
                new_items.append(item)
                if DATAPACKAGES.get(self.game) is not None:
                    classification = DATAPACKAGES[self.game].items.setdefault(r[index_item], ItemClassification.unknown)
                    if classification in [ItemClassification.progression, ItemClassification.mcguffin]:
                        self.last_progression = (r[index_item], datetime.datetime.now(tz=datetime.UTC))

        if is_up_to_date:
            return []

        self.last_item = (rows[-1][index_item], datetime.datetime.now(tz=datetime.UTC))
        self.last_recieved = datetime.datetime.now(tz=datetime.UTC)

        self.latest_item = rows[-1][index_order]
        self.new_items = new_items

        if self.filters == Filters.none:
            return []
        if self.filters in [Filters.unset, Filters.everything]:
            return new_items

        new_items = [i for i in new_items if i.classification == ItemClassification.unknown or self.filters & Filters(i.classification.value)]

        return new_items

    async def refresh_metadata(self) -> None:
        logging.info(f"Refreshing metadata for {self.url}")
        multitracker_url = "/".join(self.url.split("/")[:-2])
        async with aiohttp.ClientSession() as session:
            async with session.get(multitracker_url) as response:
                if response.status != 200:
                    self.failures += 1
                    return
                html = await response.text()
        soup = BeautifulSoup(html, features="html.parser")
        title = soup.find("title").string
        if title == "Page Not Found (404)":
            self.failures += 1
            return
        slots = process_table(soup.find(id="checks-table"))
        for slot in slots:
            if slot["#"] == self.slot_id:
                self.game = slot["Game"]
                # self.name = slot['Name']
                break

    def process_locations(self, table: Tag) -> None:
        if table is None:
            return
        rows = process_table(table)
        for r in rows:
            self.checks[r["Location"]] = bool(r["Checked"])

    def update(self, data: CheeseGame) -> None:
        self.game = data.game
        self.id = data.id
        self.progression_status = ProgressionStatus(data.progression_status)
        self.last_checked = data.last_checked
        self.last_activity = data.last_activity

    def refresh_hints(self, multiworld: "Multiworld") -> list[Hint]:
        data = multiworld.hints
        if not data:
            return []
        filters = self.hint_filters
        if filters == HintFilters.none:
            return []
        elif filters == HintFilters.unset:
            filters = HintFilters.all
        updated = []
        finder_hints = [Hint(**h, is_finder=True) for h in data if h.get("finder_game_id") == self.id]
        receiver_hints = [Hint(**h, is_finder=False) for h in data if h.get("receiver_game_id") == self.id and h.get("finder_game_id") != self.id]
        for hint in finder_hints:
            if hint.id not in self.finder_hints:
                self.finder_hints[hint.id] = hint
                if not hint.found:
                    hint.update = HintUpdate.new
                    if filters & HintFilters.finder:
                        updated.append(hint)
            elif hint.found and not self.finder_hints[hint.id].found:
                self.finder_hints[hint.id] = hint
                self.finder_hints[hint.id].update = HintUpdate.found
                if not self.finder_hints[hint.id].useless:
                    if filters & HintFilters.finder:
                        updated.append(self.finder_hints[hint.id])
            elif hint.classification != self.finder_hints[hint.id].classification and not hint.found:
                self.finder_hints[hint.id] = hint
                self.finder_hints[hint.id].update = HintUpdate.classified
                if filters & HintFilters.finder:
                    updated.append(self.finder_hints[hint.id])

            if len(updated) == 10:
                return updated

        for hint in receiver_hints:
            if hint.id not in self.receiver_hints:
                self.receiver_hints[hint.id] = hint
                if not hint.found:
                    hint.update = HintUpdate.new
                    if filters & HintFilters.receiver:
                        updated.append(hint)
            elif hint.found and not self.receiver_hints[hint.id].found:
                self.receiver_hints[hint.id] = hint
                self.receiver_hints[hint.id].update = HintUpdate.found
                if filters & HintFilters.receiver:
                    updated.append(self.receiver_hints[hint.id])
            elif hint.classification != self.receiver_hints[hint.id].classification:
                self.receiver_hints[hint.id] = hint
                self.receiver_hints[hint.id].update = HintUpdate.classified
                if filters & HintFilters.receiver:
                    updated.append(self.receiver_hints[hint.id])
            if len(updated) == 10:
                return updated

        return updated


@attrs.define()
class Multiworld:
    url: str  # https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/room_id
    tracker_id: str = None
    title: str = None
    games: dict[int, CheeseGame] = None
    last_refreshed: datetime.datetime = None
    last_update: datetime.datetime = None
    upstream_url: str = None
    room_link: str = None
    last_port: Optional[int] = None
    hints: list[dict] | None = None

    async def refresh(self, force: bool = False) -> None:
        if (
            self.last_refreshed
            and self.last_refreshed.tzinfo is not None
            and datetime.datetime.now(tz=datetime.UTC) - self.last_refreshed < datetime.timedelta(hours=1)
            and not force
        ):
            return
        self.last_refreshed = datetime.datetime.now(tz=datetime.UTC)

        logging.info(f"Refreshing {self.url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(self.url) as response:
                txt = await response.text()
        # data = requests.get(self.url).text
        data = json.loads(txt)
        self.tracker_id = data.get("tracker_id")
        self.title = data.get("title", self.title)
        self.games = {g["position"]: CheeseGame(g) for g in data.get("games")}
        GAMES.update({g.id: g for g in self.games.values()})
        MULTIWORLDS[self.tracker_id] = self
        self.last_update = datetime.datetime.fromisoformat(data.get("updated_at"))
        self.upstream_url = data.get("upstream_url")
        self.room_link = data.get("room_link")
        self.last_port = data.get("last_port")
        self.hints = data.get("hints", [])

    def last_activity(self) -> datetime.datetime:
        """
        Return the last activity time of any game in the multiworld.
        """
        return max(g.last_activity for g in self.games.values())

    def put(self, game: CheeseGame) -> None:
        # PUT https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/MMV8lMURTE6KoOLAPSs2Dw/game/63591
        game = converter.unstructure(game)  # convert datetime to isoformat
        requests.put(f"{self.url}/game/{game['id']}", json=game)

    @property
    def goaled(self) -> bool:
        return all(g.completion_status in [CompletionStatus.goal, CompletionStatus.done, CompletionStatus.released] for g in self.games.values())


def process_table(table: Tag) -> list[dict]:
    headers = [i.string for i in table.find_all("th")]
    rows = [[try_int(i) for i in r.find_all("td")] for r in table.find_all("tr")[1:]]
    return [dict(zip(headers, r)) for r in rows]


def try_int(text: Tag | str) -> str | int:
    if isinstance(text, Tag):
        if text.string:
            text = text.string
        else:
            text = text.get_text()
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return text


DATAPACKAGES: dict[str, "Datapackage"] = defaultdict(Datapackage)
GAMES: dict[int, CheeseGame] = {}
MULTIWORLDS: dict[str, Multiworld] = {}
