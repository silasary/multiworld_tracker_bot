import datetime
import enum
import json
import logging

import attrs
import requests
from bs4 import BeautifulSoup

ItemClassification = enum.Enum("ItemClassification", "unknown trap filler useful progression")


@attrs.define()
class Datapackage:
    # game: str
    items: dict[str, ItemClassification]


@attrs.define()
class TrackedGame:
    url: str  # https://archipelago.gg/tracker/tracker_id/0/slot_id
    latest_item: int = -1
    name: str = None
    game: str = None
    last_check: datetime.datetime = None
    last_update: datetime.datetime = None
    failures: int = 0

    def __hash__(self) -> int:
        return hash(self.url)

    @property
    def tracker_id(self) -> str:
        """ID of the multiworld tracker."""
        return self.url.split("/")[-3]

    @property
    def slot_id(self) -> str:
        return self.url.split("/")[-1]

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

        self.last_check = datetime.datetime.now()
        last_index = headers.index("Last Order Received")
        rows.sort(key=lambda r: r[last_index])
        if rows[-1][last_index] == self.latest_item:
            return []
        elif rows[-1][last_index] < self.latest_item:
            self.latest_item = -1
            return [("Rollback detected!",)]
        self.last_update = datetime.datetime.now()
        new_items = [r for r in rows if r[last_index] > self.latest_item]
        self.latest_item = rows[-1][last_index]
        return new_items


class CheeseGame(dict):
    @property
    def last_activity(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.get("last_activity", "1970-01-01T00:00:00Z"))

@attrs.define()
class Multiworld:
    url: str  # https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/room_id
    tracker_id: str = None
    title: str = None
    games: dict[int, CheeseGame] = None
    last_check: datetime.datetime = None
    last_update: datetime.datetime = None
    upstream_url: str = None

    async def refresh(self) -> None:
        if self.last_check and datetime.datetime.now() - self.last_check < datetime.timedelta(days=1):
            return
        self.last_check = datetime.datetime.now()

        logging.info(f"Refreshing {self.url}")
        data = requests.get(self.url).text
        data = json.loads(data)
        self.tracker_id = data.get("tracker_id")
        self.title = data.get("title")
        self.games = {g["position"]: CheeseGame(g) for g in data.get("games")}
        self.last_update = datetime.datetime.fromisoformat(data.get("updated_at"))
        self.upstream_url = data.get("upstream_url")

    def last_activity(self) -> datetime.datetime:
        return max(g.last_activity for g in self.games.values())



def try_int(text: str) -> str | int:
    try:
        return int(text)
    except ValueError:
        return text

