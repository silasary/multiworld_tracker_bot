import datetime
import enum
import json
import logging
from typing import Optional
import urllib.parse

import aiohttp
from collections import defaultdict

import attrs
from bs4 import BeautifulSoup, Tag

from ap_alert.models.enums import ProgressionStatus, HintClassification, HintUpdate, TrackerStatus, CompletionStatus
from archipelagopy.netutils import fetch_datapackage_from_webhost
from world_data.models import Datapackage, ItemClassification


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
    useful_plus_progression = useful | progression
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
    flags: ItemClassification = ItemClassification.unknown

    @property
    def classification(self) -> ItemClassification:
        if self.flags != ItemClassification.unknown:
            return self.flags
        return DATAPACKAGES[self.game].items.get(self.name, ItemClassification.unknown)


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
class TrackedGame:
    url: str  # https://archipelago.gg/tracker/tracker_id/0/slot_id
    id: int = -1
    latest_item: int = -1
    disabled: bool = False

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

    notification_queue: list[NetworkItem] = attrs.field(factory=list, repr=False)

    def __hash__(self) -> int:
        return hash(self.url)

    @property
    def tracker_id(self) -> str:
        """ID of the multiworld tracker."""
        return self.url.split("/")[-3]

    @property
    def slot_id(self) -> int:
        return int(self.url.split("/")[-1])

    async def refresh_metadata(self) -> None:
        logging.info(f"Refreshing metadata for {self.url}")
        multitracker_url = self.multitracker_url
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

    @property
    def multitracker_url(self):
        multitracker_url = "/".join(self.url.split("/")[:-2])
        return multitracker_url

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
                if filters & HintFilters.finder and hint.classification != HintClassification.unset:
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
            elif hint.classification != self.receiver_hints[hint.id].classification and not hint.found:
                self.receiver_hints[hint.id] = hint
                self.receiver_hints[hint.id].update = HintUpdate.classified
                if filters & HintFilters.receiver and hint.classification != HintClassification.unset:
                    updated.append(self.receiver_hints[hint.id])
            if len(updated) == 10:
                return updated

        return updated


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
        async with aiohttp.ClientSession() as session:
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
                async with aiohttp.ClientSession() as session:
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
        async with aiohttp.ClientSession() as session:
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
        async with aiohttp.ClientSession() as session:
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
            async with aiohttp.ClientSession() as session:
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
            slot.all_items[r[index_item]] = r[index_amount]
            if r[index_order] > slot.latest_item:
                item = NetworkItem(r[index_item], slot.game, 1)
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
        async with aiohttp.ClientSession() as session:
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
        if self.mw.player_items_received is None:
            await self.refresh()
            if self.mw.player_items_received is None:
                return False
        new_items: list[NetworkItem] = []
        api_items = self.mw.player_items_received[slot.slot_id - 1]["items"]

        # new_items = [NetworkItem(i["item"], slot.game, i["count"], ItemClassification.from_network_flag(i['flags'])) for i in api_items if i["order"] > slot.latest_item]
        if len(api_items) - 1 == slot.latest_item:
            return False

        checksum = self.mw.static_tracker_data["datapackage"].get(slot.game, {}).get("checksum")
        ap_datapackage = await fetch_datapackage_from_webhost(slot.game, checksum)
        if not ap_datapackage:
            logging.warning(f"Could not load datapackage for game {slot.game} with checksum {checksum}")
            self.enabled = False
            return False
        if "item_id_to_name" not in ap_datapackage:
            ap_datapackage["item_id_to_name"] = {v: k for k, v in ap_datapackage.get("item_name_to_id", {}).items()}

        for index, i in enumerate(api_items, start=0):
            if index > slot.latest_item:
                item_id = i[0]
                #  location = i[1]
                #  sender = i[2]
                flags = i[3] if len(i) > 3 else 0

                item_name = ap_datapackage["item_id_to_name"].get(item_id, str(item_id))
                item = NetworkItem(item_name, slot.game, 1, ItemClassification.from_network_flag(flags))
                new_items.append(item)
                if item.classification in [ItemClassification.progression, ItemClassification.mcguffin]:
                    slot.last_progression = (item_name, datetime.datetime.now(tz=datetime.UTC))

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
MULTIWORLDS_BY_CHEESE: dict[str, Multiworld] = {}
MULTIWORLDS_BY_AP: dict[str, Multiworld] = {}
