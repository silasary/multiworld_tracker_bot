import datetime
import json
import logging
from typing import Optional
import urllib.parse

import aiohttp
from collections import defaultdict

import attrs
from bs4 import BeautifulSoup

from ap_alert.models.network_item import NetworkItem
from ap_alert.models.cheese_game import CheeseGame
from ap_alert.models.enums import Filters
from ap_alert.models.tracked_game import TrackedGame
from ap_alert.models.enums import CompletionStatus
from archipelagopy import netutils
from archipelagopy.utils import fetch_datapackage_from_webhost
from shared.bs_helpers import process_table
from world_data.models import Datapackage, ItemClassification
from shared.web import make_session


@attrs.define()
class Multiworld:
    url: str
    cheese_url: str | None = None
    tracker_id: str | None = None
    ap_tracker_id: str | None = None
    cheese_tracker_id: str | None = None
    title: str | None = None
    games: dict[int, CheeseGame] = attrs.field(factory=dict)
    last_refreshed: datetime.datetime = attrs.field(factory=lambda: datetime.datetime.fromisoformat("1970-01-01T00:00:00Z"))
    last_update: datetime.datetime = attrs.field(factory=lambda: datetime.datetime.fromisoformat("1970-01-01T00:00:00Z"))
    upstream_url: str | None = None
    room_link: str | None = None
    last_port: Optional[int] = None
    hints: list[dict] | None = attrs.field(factory=list)
    player_checks_done: list[dict] = attrs.field(factory=list, init=False)
    player_items_received: list[dict] = attrs.field(factory=list, init=False)

    static_tracker_data: dict | None = attrs.field(init=False, repr=False, default=None)
    slot_data: list[dict] | None = attrs.field(init=False, repr=False, default=None)

    agents: dict[str, "BaseAgent"] = attrs.field(factory=dict, repr=False, init=False)

    async def refresh(self, force: bool = False) -> None:
        if "cheese" not in self.agents:
            self.agents["cheese"] = CheeseAgent(self)
        await self.agents["cheese"].refresh(force)
        if "api" not in self.agents:
            self.agents["api"] = ApiTrackerAgent(self)
        await self.agents["api"].refresh(force)
        if not self.agents["cheese"].enabled and not self.agents["api"].enabled:
            if "webtracker" not in self.agents:
                self.agents["webtracker"] = WebTrackerAgent(self)
            await self.agents["webtracker"].refresh(force)

        self.last_refreshed = datetime.datetime.now(tz=datetime.UTC)
        if self.cheese_tracker_id is not None and MULTIWORLDS_BY_CHEESE.get(self.cheese_tracker_id) is not self:
            MULTIWORLDS_BY_CHEESE[self.cheese_tracker_id] = self
        if self.ap_tracker_id is not None and MULTIWORLDS_BY_AP.get(self.ap_tracker_id) is not self:
            MULTIWORLDS_BY_AP[self.ap_tracker_id] = self

    async def refresh_game(self, slot: TrackedGame) -> bool:
        if "api" in self.agents and self.agents["api"].enabled:
            return await self.agents["api"].refresh_game(slot)

        if "webtracker" not in self.agents:
            self.agents["webtracker"] = WebTrackerAgent(self)
        if self.agents["webtracker"].enabled:
            return await self.agents["webtracker"].refresh_game(slot)
        return False

    def last_activity(self) -> datetime.datetime:
        """
        Return the last activity time of any game in the multiworld.
        """
        if not self.games:
            return datetime.datetime.fromisoformat("1970-01-01T00:00:00Z")
        return max(g.last_activity for g in self.games.values())

    async def put(self, game: CheeseGame) -> None:
        # PUT https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/MMV8lMURTE6KoOLAPSs2Dw/game/63591
        from .converter import converter

        game = converter.unstructure(game)  # convert datetime to isoformat
        async with make_session() as session:
            async with session.put(f"{self.url}/game/{game['id']}", json=game) as response:
                if response.status != 200:
                    raise aiohttp.ClientResponseError(
                        status=response.status,
                        message=f"Failed to update game {game['id']}",
                        request_info=response.request_info,
                        history=response.history,
                    )

    @property
    def goaled(self) -> bool:
        return all(g.completion_status in [CompletionStatus.goal, CompletionStatus.done, CompletionStatus.released] for g in self.games.values())

    @property
    def ap_hostname(self) -> str:
        """
        Return the hostname of the AP server.
        """
        if self.upstream_url:
            uri = urllib.parse.urlparse(self.upstream_url)
            return uri.hostname or "archipelago.gg"
        return "archipelago.gg"

    @property
    def ap_scheme(self) -> str:
        """
        Return the scheme of the AP server.
        """
        if self.upstream_url:
            uri = urllib.parse.urlparse(self.upstream_url)
            return uri.scheme or "https"
        return "https"


class BaseAgent:
    mw: Multiworld
    enabled: bool = True
    last_refreshed: datetime.datetime = datetime.datetime.fromisoformat("1970-01-01T00:00:00Z")

    def __init__(self, mw: Multiworld) -> None:
        self.mw = mw

    def rate_limit(self, min_interval: datetime.timedelta, force: bool) -> bool:
        if not self.enabled:
            return True
        if force:
            self.last_refreshed = datetime.datetime.now(tz=datetime.UTC)
            return False
        if self.last_refreshed and datetime.datetime.now(tz=datetime.UTC) - self.last_refreshed < min_interval:
            return True
        self.last_refreshed = datetime.datetime.now(tz=datetime.UTC)
        return False

    async def refresh(self, force: bool = False) -> None:
        raise NotImplementedError()

    async def refresh_game(self, slot: TrackedGame) -> bool:
        raise NotImplementedError()


class CheeseAgent(BaseAgent):
    async def refresh(self, force: bool = False) -> None:
        if self.rate_limit(datetime.timedelta(hours=1), force):
            return

        if self.mw.cheese_url is None:
            if self.mw.url.startswith("https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/"):
                self.mw.cheese_url = self.mw.url
            else:
                async with make_session() as session:
                    async with session.post(
                        "https://cheesetrackers.theincrediblewheelofchee.se/api/tracker",
                        json={"url": self.mw.url},
                    ) as response:
                        if response.status in [400, 404, 403]:
                            self.enabled = False
                            return
                        ch_id = (await response.json()).get("tracker_id")
                self.mw.cheese_url = f"https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/{ch_id}"

        logging.info(f"Refreshing {self.mw.cheese_url}")
        async with make_session() as session:
            async with session.get(self.mw.cheese_url) as response:
                txt = await response.text()
        data = json.loads(txt)
        self.mw.cheese_tracker_id = data.get("tracker_id")
        self.mw.title = data.get("title", self.mw.title)
        self.mw.games = {g["position"]: CheeseGame(g) for g in data.get("games")}
        GAMES.update({g.id: g for g in self.mw.games.values()})
        self.mw.last_update = datetime.datetime.fromisoformat(data.get("updated_at"))
        self.mw.upstream_url = data.get("upstream_url")
        self.mw.room_link = data.get("room_link")
        self.mw.last_port = data.get("last_port")
        self.mw.hints = data.get("hints", [])

        if self.mw.url.startswith("https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/") and self.mw.upstream_url is not None:
            self.mw.url = self.mw.upstream_url


class WebTrackerAgent(BaseAgent):
    async def refresh(self, force: bool = False) -> None:
        if self.rate_limit(datetime.timedelta(hours=1), force):
            return

        if self.mw.upstream_url is None:
            self.mw.upstream_url = self.mw.url
        if self.mw.games is None:
            self.mw.games = {}
        if self.mw.room_link == "None":
            self.mw.room_link = None
        self.mw.ap_tracker_id = self.mw.url.split("/")[-1]
        logging.info(f"Refreshing cheeseless {self.mw.url}")
        multitracker_url = self.mw.url
        async with make_session() as session:
            try:
                async with session.get(multitracker_url) as response:
                    if response.status != 200:
                        return
                    html = await response.text()
            except aiohttp.ClientConnectorError as e:
                logging.error(f"Connection error occurred while processing tracker {self.mw.url}: {e}")
                return
            except aiohttp.ConnectionTimeoutError as e:
                logging.error(f"Connection timeout error occurred while processing tracker {self.mw.url}: {e}")
                self.last_refreshed = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(hours=24)  # back off for a day
                return
        soup = BeautifulSoup(html, features="html.parser")
        title = soup.find("title").string
        if title == "Page Not Found (404)":
            self.enabled = False
            return
        slots = process_table(soup.find(id="checks-table"))
        for slot in slots:
            slot_id = slot["#"]
            if slot_id == "Total":
                continue
            inactivity = slot.get(None, None)
            checks_done, checks_total = slot.get("Checks", "0/0").split("/")
            if inactivity is not None and inactivity != "None":
                last_activity = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(seconds=float(inactivity))
            else:
                last_activity = datetime.datetime.fromisoformat(slot.get("Last Activity", "1970-01-01T00:00:00Z"))

            if slot_id not in self.mw.games:
                self.mw.games[slot_id] = CheeseGame({"name": slot["Name"], "game": slot["Game"], "position": int(slot_id)})
            self.mw.games[slot_id].update({"game": slot["Game"], "last_activity": last_activity.isoformat(), "checks_done": int(checks_done), "checks_total": int(checks_total)})

        self.mw.last_update = max(g.last_activity for g in self.mw.games.values())

    async def refresh_game(self, slot: TrackedGame) -> bool:
        if not self.enabled:
            return False

        if slot.game is None or slot.game == "None":
            await slot.refresh_metadata()
        logging.info(f"Refreshing {slot.url}")
        try:
            async with make_session() as session:
                async with session.get(slot.url) as response:
                    if response.status == 500 and "/tracker/" in slot.url:
                        slot.url = slot.url.replace("/tracker/", "/generic_tracker/")
                        return await self.refresh_game(slot)

                    if response.status != 200:
                        slot.failures += 1
                        return False
                    html = await response.text()
        except aiohttp.InvalidUrlClientError:
            # This is a bad URL, don't try again
            slot.failures = 100
            return False
        except aiohttp.ConnectionTimeoutError as e:
            logging.error(f"Connection timeout error occurred while processing tracker {slot.url}: {e}")
            slot.failures += 1
            return False
        except aiohttp.ClientConnectorError as e:
            logging.error(f"Connection error occurred while processing tracker {slot.url}: {e}")
            slot.failures += 1
            return False
        soup = BeautifulSoup(html, features="html.parser")
        title = soup.find("title").string
        if title == "Page Not Found (404)":
            slot.failures += 1
            return False
        recieved = soup.find(id="received-table")
        if recieved is None:
            if "/tracker/" in slot.url:
                slot.url = slot.url.replace("/tracker/", "/generic_tracker/")
                return await self.refresh_game(slot)
        # headers = [i.string for i in recieved.find_all("th")]
        # rows = [[try_int(i.string) for i in r.find_all("td")] for r in recieved.find_all("tr")[1:]]
        slot.process_locations(soup.find(id="locations-table"))
        rows = process_table(recieved)
        slot.last_refresh = datetime.datetime.now(tz=datetime.timezone.utc)
        if not rows:
            return False

        index_order = "Last Order Received"
        index_amount = "Amount"
        index_item = "Item"

        rows.sort(key=lambda r: r[index_order])
        if rows[-1][index_order] < slot.latest_item:
            slot.latest_item = -1
            return False
        is_up_to_date = rows[-1][index_order] == slot.latest_item
        if is_up_to_date and slot.all_items:
            return False

        new_items: list[NetworkItem] = []
        for r in rows:
            item = NetworkItem(r[index_item], slot.game, r[index_amount])
            slot.all_items.append(item)
            if r[index_order] > slot.latest_item:
                new_items.append(item)
                if DATAPACKAGES.get(slot.game) is not None:
                    classification = DATAPACKAGES[slot.game].items.setdefault(r[index_item], ItemClassification.unknown)
                    if classification in [ItemClassification.progression, ItemClassification.mcguffin]:
                        slot.last_progression = (r[index_item], datetime.datetime.now(tz=datetime.UTC))

        if is_up_to_date:
            return False

        slot.last_item = (rows[-1][index_item], datetime.datetime.now(tz=datetime.UTC))
        slot.last_recieved = datetime.datetime.now(tz=datetime.UTC)

        slot.latest_item = rows[-1][index_order]
        slot.new_items = new_items

        slot.failures = 0

        if slot.filters == Filters.none:
            return False
        if slot.filters in [Filters.unset, Filters.everything]:
            slot.notification_queue.extend(new_items)
            return True

        new_items = [i for i in new_items if i.classification in [ItemClassification.unknown, ItemClassification.bad_name] or slot.filters & Filters(i.classification.value)]

        slot.notification_queue.extend(new_items)
        return bool(new_items)


class ApiTrackerAgent(BaseAgent):
    async def refresh(self, force: bool = False) -> None:
        if self.rate_limit(datetime.timedelta(hours=1), force):
            return

        if self.mw.ap_tracker_id is None:
            self.mw.ap_tracker_id = self.mw.url.split("/")[-1]

        logging.info(f"Refreshing API multiworld {self.mw.url}")
        async with make_session() as session:
            if self.mw.static_tracker_data is None:
                static_url = f"{self.mw.ap_scheme}://{self.mw.ap_hostname}/api/static_tracker/{self.mw.ap_tracker_id}"
                async with session.get(static_url) as response:
                    if response.status != 200:
                        self.enabled = False
                        return
                    self.mw.static_tracker_data = await response.json()
            if self.mw.slot_data is None:
                slot_url = f"{self.mw.ap_scheme}://{self.mw.ap_hostname}/api/slot_data_tracker/{self.mw.ap_tracker_id}"
                async with session.get(slot_url) as response:
                    if response.status == 500:
                        # Temporary hack
                        self.mw.slot_data = []
                    elif response.status != 200:
                        self.enabled = False
                        return
                    else:
                        self.mw.slot_data = await response.json()
            api_url = f"{self.mw.ap_scheme}://{self.mw.ap_hostname}/api/tracker/{self.mw.ap_tracker_id}"
            async with session.get(api_url) as response:
                if response.status != 200:
                    self.enabled = False
                    return
                data = await response.json()
        self.mw.player_checks_done = data.get("player_checks_done", [])
        self.mw.player_items_received = data.get("player_items_received", [])

    async def refresh_game(self, slot: TrackedGame) -> bool:
        if self.mw.player_items_received is None or not slot.all_items:
            await self.refresh()
            if self.mw.player_items_received is None:
                return False
        new_items: list[NetworkItem] = []
        all_items: list[NetworkItem] = []
        api_items: list[netutils.NetworkItem] = next((i["items"] for i in self.mw.player_items_received if i["player"] == slot.slot_id), [])

        if len(api_items) - 1 == slot.latest_item and slot.all_items:
            return False
        if len(api_items) < slot.latest_item:
            logging.error(f"Rollback detected in {slot.url}")
            slot.latest_item = len(api_items) - 1
            return False

        checksum = self.mw.static_tracker_data["datapackage"].get(slot.game, {}).get("checksum")
        if checksum:
            ap_datapackage = await fetch_datapackage_from_webhost(slot.game, checksum)
        else:
            ap_datapackage = None
        if not ap_datapackage:
            logging.warning(f"Could not load datapackage for game {slot.game} with checksum {checksum}")
            self.enabled = False
            return False
        if "item_id_to_name" not in ap_datapackage:
            ap_datapackage["item_id_to_name"] = {v: k for k, v in ap_datapackage.get("item_name_to_id", {}).items()}

        for index, netitem in enumerate(api_items, start=0):
            item_id = netitem[0]
            #  location = netitem[1]
            #  sender = netitem[2]
            flags = netitem[3] if len(netitem) > 3 else 0

            item_name = ap_datapackage["item_id_to_name"].get(item_id, str(item_id))
            classification = ItemClassification.from_network_flag(flags)
            classification = DATAPACKAGES[slot.game].postprocess_item_classification(item_name, classification)
            item = NetworkItem(item_name, slot.game, 1, classification)
            all_items.append(item)
            if index > slot.latest_item:
                new_items.append(item)
                if item.classification in [ItemClassification.progression, ItemClassification.mcguffin]:
                    slot.last_progression = (item_name, datetime.datetime.now(tz=datetime.UTC))

        slot.last_refresh = datetime.datetime.now(tz=datetime.timezone.utc)
        slot.all_items = all_items
        if not new_items:
            return False

        slot.last_item = (new_items[-1].name, datetime.datetime.now(tz=datetime.UTC))
        slot.latest_item = len(api_items) - 1
        slot.last_recieved = datetime.datetime.now(tz=datetime.UTC)

        slot.failures = 0

        if slot.filters == Filters.none:
            return False
        if slot.filters in [Filters.unset, Filters.everything]:
            slot.notification_queue.extend(new_items)
            return True

        new_items = [i for i in new_items if i.classification in [ItemClassification.unknown, ItemClassification.bad_name] or slot.filters & Filters(i.classification.value)]
        slot.notification_queue.extend(new_items)
        return bool(new_items)


DATAPACKAGES: dict[str, "Datapackage"] = defaultdict(Datapackage)
GAMES: dict[int, CheeseGame] = {}
MULTIWORLDS_BY_CHEESE: dict[str, Multiworld] = {}
MULTIWORLDS_BY_AP: dict[str, Multiworld] = {}
