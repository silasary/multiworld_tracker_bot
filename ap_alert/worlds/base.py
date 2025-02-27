from typing import TYPE_CHECKING
from interactions import Embed


if TYPE_CHECKING:
    from ap_alert.multiworld import TrackedGame


class CustomTracker:
    async def build_dashboard(self, tracker: "TrackedGame") -> Embed:
        return None
