from typing import TYPE_CHECKING, Optional
from interactions import Embed


if TYPE_CHECKING:
    from ap_alert.multiworld import TrackedGame
    from world_data.models import ItemClassification


class CustomTracker:
    async def build_dashboard(self, tracker: "TrackedGame") -> Embed:
        return None

    async def classify(self, tracker: "TrackedGame", item_name: str) -> Optional["ItemClassification"]:
        return None
