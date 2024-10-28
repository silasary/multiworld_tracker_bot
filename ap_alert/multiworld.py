import datetime
import enum
import json
import logging
from typing import Optional
from shared.cursed_enum import CursedStrEnum
from collections import defaultdict

import attrs
import requests
from bs4 import BeautifulSoup

from .converter import converter

OldClassification = enum.Enum("OldClassification", "unknown trap filler useful progression mcguffin")
ProgressionStatus = CursedStrEnum("ProgressionStatus", "unknown bk go soft_bk unblocked")
HintClassification = CursedStrEnum("HintClassification", "unknown critical useful trash")
HintUpdate = CursedStrEnum("HintUpdate", "none new found classified useless")

@attrs.define()
class Hint:
    id: int
    finder_game_id: int
    receiver_game_id: int
    item: str
    location: str
    entrance: str
    found: bool
    classification: HintClassification
    update: HintUpdate = attrs.field(default=HintUpdate.none, init=False)

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
        item = f'{DATAPACKAGES[receiver.game].icon(self.item)} {self.item}'
        description = f"***{receiver.name}***'s ***{item}*** is at ***{self.location}***"
        if self.entrance and self.entrance != "Vanilla":
            description += f" ({self.entrance})"

        return {
            "title": title,
            "description": description,
            # "color": self.classification.color,
            # "footer": {"text": f"Hint ID: {self.id}"},
        }


class ItemClassification(enum.Flag):
    unknown = 0
    trap = 1
    filler = 2
    useful = 4
    progression = 8
    mcguffin = 16

    bad_name = 256


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


@attrs.define()
class NetworkItem:
    name: str
    game: str
    quantity: int

    @property
    def classification(self) -> ItemClassification:
        return DATAPACKAGES[self.game].items.get(self.name, ItemClassification.unknown)

@attrs.define()
class OldDatapackage:
    # game: str
    items: dict[str, OldClassification]

@attrs.define()
class Datapackage:
    items: dict[str, ItemClassification] = attrs.field(factory=dict)

    def icon(self, item_name: str) -> str:
        classification = self.items.get(item_name, ItemClassification.unknown)
        emoji = "❓"
        if classification == ItemClassification.mcguffin:
            emoji =  "✨"
        if classification == ItemClassification.filler:
            emoji = "<:filler:1277502385459171338>"
        if classification == ItemClassification.useful:
            emoji = "<:useful:1277502389729103913>"
        if classification == ItemClassification.progression:
            emoji = "<:progression:1277502382682542143>"
        if classification == ItemClassification.trap:
            emoji = "❌"
        return emoji

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
    def last_activity(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.get("last_activity", "1970-01-01T00:00:00Z"))

    @property
    def name(self) -> str:
        return self.get("name", self.get("position", "Unknown"))


@attrs.define()
class TrackedGame:
    url: str  # https://archipelago.gg/tracker/tracker_id/0/slot_id
    id: int = -1
    latest_item: int = -1

    name: str = None
    game: str = None
    last_refresh: datetime.datetime = None
    last_update: datetime.datetime = None
    failures: int = 0
    filters: Filters = Filters.unset

    last_progression: tuple[str, datetime.datetime] = attrs.field(factory=lambda: ("", datetime.datetime.fromisoformat("1970-01-01T00:00:00Z")))
    last_item: tuple[str, datetime.datetime] = attrs.field(factory=lambda: ("", datetime.datetime.fromisoformat("1970-01-01T00:00:00Z")))
    progression_status: ProgressionStatus = ProgressionStatus.unknown

    all_items: dict[str, int] = attrs.field(factory=dict, init=False)
    new_items: list[NetworkItem] = attrs.field(factory=list, init=False)

    # hints: list[Hint] = attrs.field(factory=list)
    finder_hints: dict[int, Hint] = attrs.field(factory=dict)
    receiver_hints: dict[int, Hint] = attrs.field(factory=dict)


    def __hash__(self) -> int:
        return hash(self.url)

    @property
    def tracker_id(self) -> str:
        """ID of the multiworld tracker."""
        return self.url.split("/")[-3]

    @property
    def slot_id(self) -> int:
        return int(self.url.split("/")[-1])

    def refresh(self) -> list[NetworkItem]:
        logging.info(f"Refreshing {self.url}")
        html = requests.get(self.url).content
        soup = BeautifulSoup(html, features="html.parser")
        title = soup.find("title").string
        if title == "Page Not Found (404)":
            self.failures += 1
            return []
        recieved = soup.find(id="received-table")
        if recieved is None:
            if '/tracker/' in self.url:
                self.url = self.url.replace('/tracker/', '/generic_tracker/')
                return self.refresh()
        headers = [i.string for i in recieved.find_all("th")]
        rows = [[try_int(i.string) for i in r.find_all("td")] for r in recieved.find_all("tr")[1:]]
        if not rows:
            return []

        index_order = headers.index("Last Order Received")
        index_amount = headers.index("Amount")
        index_item = headers.index("Item")

        self.last_refresh = datetime.datetime.now()
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
                    classification = DATAPACKAGES[self.game].items.get(r[0])
                    if classification in [ItemClassification.progression, ItemClassification.mcguffin]:
                        self.last_progression = (r[0], datetime.datetime.now())

        if is_up_to_date:
            return []

        self.last_item = (rows[-1][0], datetime.datetime.now())
        self.last_update = datetime.datetime.now()

        self.latest_item = rows[-1][index_order]
        self.new_items = new_items

        if self.filters == Filters.none:
            return []
        if self.filters in [Filters.unset, Filters.everything]:
            return new_items

        new_items = [i for i in new_items if i.classification == ItemClassification.unknown or self.filters & Filters(i.classification.value)]

        return new_items

    def update(self, data: CheeseGame) -> None:
        self.game = data.game
        self.id = data.id
        self.progression_status = ProgressionStatus(data.progression_status)

    def refresh_hints(self, multiworld: "Multiworld") -> list[Hint]:
        data = multiworld.hints
        # hints = [Hint(**h) for h in data if h["receiver_game_id"] == self.id or h["finder_game_id"] == self.id]
        updated = []
        finder_hints = [Hint(**h) for h in data if h["finder_game_id"] == self.id]
        receiver_hints = [Hint(**h) for h in data if h["receiver_game_id"] == self.id]
        for hint in finder_hints:
            if hint.id not in self.finder_hints:
                self.finder_hints[hint.id] = hint
                if not hint.found:
                    hint.update = HintUpdate.new
                    updated.append(hint)
            elif hint.found and not self.finder_hints[hint.id].found:
                self.finder_hints[hint.id].found = True
                self.finder_hints[hint.id].update = HintUpdate.found
                updated.append(self.finder_hints[hint.id])
            elif hint.classification != self.finder_hints[hint.id].classification:
                self.finder_hints[hint.id].classification = hint.classification
                self.finder_hints[hint.id].update = HintUpdate.classified
                updated.append(self.finder_hints[hint.id])

        for hint in receiver_hints:
            if hint.id not in self.receiver_hints:
                self.receiver_hints[hint.id] = hint
                if not hint.found:
                    hint.update = HintUpdate.new
                    updated.append(hint)
            elif hint.found and not self.receiver_hints[hint.id].found:
                self.receiver_hints[hint.id].found = True
                self.receiver_hints[hint.id].update = HintUpdate.found
                updated.append(self.receiver_hints[hint.id])
            elif hint.classification != self.receiver_hints[hint.id].classification:
                self.receiver_hints[hint.id].classification = hint.classification
                self.receiver_hints[hint.id].update = HintUpdate.classified
                updated.append(self.receiver_hints[hint.id])

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
    room_url: str = None
    last_port: Optional[int] = None
    hints: list[dict] = None

    async def refresh(self, force: bool = False) -> None:
        if self.last_refreshed and datetime.datetime.now() - self.last_refreshed < datetime.timedelta(hours=1) and not force:
            return
        self.last_refreshed = datetime.datetime.now()

        logging.info(f"Refreshing {self.url}")
        data = requests.get(self.url).text
        data = json.loads(data)
        self.tracker_id = data.get("tracker_id")
        self.title = data.get("title", self.title)
        self.games = {g["position"]: CheeseGame(g) for g in data.get("games")}
        GAMES.update({g.id: g for g in self.games.values()})
        self.last_update = datetime.datetime.fromisoformat(data.get("updated_at"))
        self.upstream_url = data.get("upstream_url")
        self.room_url = data.get("room_url")
        self.last_port = data.get("last_port")
        self.hints = data.get("hints", [])


    def last_activity(self) -> datetime.datetime:
        return max(g.last_activity for g in self.games.values())

    def put(self, game: CheeseGame) -> None:
        # PUT https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/MMV8lMURTE6KoOLAPSs2Dw/game/63591
        game = converter.unstructure(game)  # convert datetime to isoformat
        requests.put(f"{self.url}/game/{game['id']}", json=game)

def try_int(text: str) -> str | int:
    try:
        return int(text)
    except ValueError:
        return text

DATAPACKAGES: dict[str, "Datapackage"] = defaultdict(Datapackage)
GAMES: dict[int, CheeseGame] = {}
