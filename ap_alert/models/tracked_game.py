from typing import TYPE_CHECKING
from ap_alert.models.network_item import NetworkItem
from ap_alert.models.hint import Hint
from ap_alert.models.cheese_game import CheeseGame
from ap_alert.models.enums import Filters, HintClassification, HintFilters, HintUpdate, ProgressionStatus
from shared.bs_helpers import process_table
from shared.web import make_session

if TYPE_CHECKING:
    from ap_alert.multiworld import Multiworld


import attrs
from bs4 import BeautifulSoup, Tag


import datetime
import logging


@attrs.define()
class TrackedGame:
    url: str  # https://archipelago.gg/tracker/tracker_id/0/slot_id
    _id: str | None = attrs.field(default=None)
    user_id: int = -1
    cheese_id: int = -1
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

    all_items: list[NetworkItem] = attrs.field(factory=list, init=False, repr=False)
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
        async with make_session() as session:
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

    def update(self, data: "CheeseGame") -> None:
        self.game = data.game
        self.cheese_id = data.id
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
        finder_hints = [Hint(**h, is_finder=True) for h in data if h.get("finder_game_id") == self.cheese_id]
        receiver_hints = [Hint(**h, is_finder=False) for h in data if h.get("receiver_game_id") == self.cheese_id and h.get("finder_game_id") != self.cheese_id]
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
