import typing
from world_data.models import ItemClassification
from .base import CustomTracker

if typing.TYPE_CHECKING:
    from ap_alert.multiworld import TrackedGame


class SlotLock(CustomTracker):
    async def classify(self, tracker: "TrackedGame", item_name: str) -> ItemClassification | None:
        if item_name.startswith("Unlock "):
            return ItemClassification.progression

        return await super().classify(tracker, item_name)
