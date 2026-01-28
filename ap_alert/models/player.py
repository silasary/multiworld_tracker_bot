from ap_alert.models.enums import Filters, HintFilters
from ap_alert.multiworld import MULTIWORLDS_BY_CHEESE, Multiworld
from shared.exceptions import BadAPIKeyException


import attrs
import interactions

from shared.web import make_session


@attrs.define()
class Player:
    id: int
    name: str = None

    cheese_api_key: str | None = None
    default_filters: Filters = Filters.unset
    default_hint_filters: HintFilters = HintFilters.unset
    quiet_mode: bool = False

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    def __str__(self) -> str:
        return self.name if self.name else self.mention

    async def get_trackers(self) -> list["Multiworld"]:
        async with make_session() as session:
            headers = {"Authorization": f"Bearer {self.cheese_api_key}"} if self.cheese_api_key else {}
            async with session.get("https://cheesetrackers.theincrediblewheelofchee.se/api/dashboard/tracker", headers=headers) as response:
                if response.status == 401:
                    raise BadAPIKeyException("Invalid API key.")
                data = await response.json()
        value = []
        for tracker in data:
            url = f"https://cheesetrackers.theincrediblewheelofchee.se/api/tracker/{tracker['tracker_id']}"
            if MULTIWORLDS_BY_CHEESE.get(tracker["tracker_id"]) is not None:
                value.append(MULTIWORLDS_BY_CHEESE[tracker["tracker_id"]])
            else:
                value.append(Multiworld(url))
        return value

    def update(self, user: interactions.User) -> None:
        self.name = user.global_name
