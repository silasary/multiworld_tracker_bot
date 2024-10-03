import datetime
import enum
import json
import logging
from shared.cursed_enum import CursedStrEnum

import attrs
import requests
from bs4 import BeautifulSoup

from .converter import converter

ItemClassification = enum.Enum("ItemClassification", "unknown trap filler useful progression")
ProgressionStatus = CursedStrEnum("ProgressionStatus", "unknown bk go soft_bk unblocked")

class Fiters(enum.Flag):
    none = 0
    trap = 1
    filler = 2
    useful = 4
    progression = 8

    everything = trap | filler | useful | progression
    useful_plus = useful | progression



DATAPACKAGES: dict[str, "Datapackage"] = {}


@attrs.define()
class Datapackage:
    # game: str
    items: dict[str, ItemClassification]

class CheeseGame(dict):
    @property
    def last_activity(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.get("last_activity", "1970-01-01T00:00:00Z"))


@attrs.define()
class TrackedGame:
    url: str  # https://archipelago.gg/tracker/tracker_id/0/slot_id
    id: int = -1
    latest_item: int = -1
    name: str = None
    game: str = None
    last_check: datetime.datetime = None
    last_update: datetime.datetime = None
    failures: int = 0
    last_progression: tuple[str, datetime.datetime] = attrs.field(factory=lambda: ("", datetime.datetime.fromisoformat("1970-01-01T00:00:00Z")))
    progression_status: ProgressionStatus = ProgressionStatus.unknown
    all_items: dict[str, int] = attrs.field(factory=dict, init=False)
    new_items: list[list[str]] = attrs.field(factory=list, init=False)

    def __hash__(self) -> int:
        return hash(self.url)

    @property
    def tracker_id(self) -> str:
        """ID of the multiworld tracker."""
        return self.url.split("/")[-3]

    @property
    def slot_id(self) -> int:
        return int(self.url.split("/")[-1])

    def refresh(self) -> list[list[str]]:
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

        self.last_check = datetime.datetime.now()
        rows.sort(key=lambda r: r[index_order])
        if rows[-1][index_order] == self.latest_item:
            return []
        elif rows[-1][index_order] < self.latest_item:
            self.latest_item = -1
            return [("Rollback detected!",)]
        self.last_update = datetime.datetime.now()
        new_items = []
        for r in rows:
            self.all_items[r[index_item]] = r[index_amount]
            if r[index_order] > self.latest_item:
                new_items.append(r)
                if DATAPACKAGES.get(self.game) is not None:
                    classification = DATAPACKAGES[self.game].items.get(r[0])
                    if classification == ItemClassification.progression:
                        self.last_progression = (r[0], datetime.datetime.now())

        self.latest_item = rows[-1][index_order]
        self.new_items = new_items
        return new_items

    def update(self, data: CheeseGame) -> None:
        self.game = data["game"]
        self.id = data["id"]
        self.progression_status = ProgressionStatus(data["progression_status"])


@attrs.define()
class Multiworld:
    url: str  # https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/room_id
    tracker_id: str = None
    title: str = None
    games: dict[int, CheeseGame] = None
    last_refreshed: datetime.datetime = None
    last_update: datetime.datetime = None
    upstream_url: str = None

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
        self.last_update = datetime.datetime.fromisoformat(data.get("updated_at"))
        self.upstream_url = data.get("upstream_url")

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

