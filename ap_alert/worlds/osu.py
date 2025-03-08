from typing import TYPE_CHECKING
from interactions import Embed


if TYPE_CHECKING:
    from ap_alert.multiworld import TrackedGame


from .base import CustomTracker


class OsuTracker(CustomTracker):
    async def build_dashboard(self, tracker: "TrackedGame") -> Embed:
        unplayed = set([song[:-9] for song, complete in tracker.checks.items() if not complete])
        recieved = set(tracker.all_items.keys())
        unlocked = unplayed & recieved
        return Embed(
            title="Osu!",
            description=f"{len(unlocked)} songs available to play",
        )
