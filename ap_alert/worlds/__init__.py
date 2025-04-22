from .base import CustomTracker
from .muse_dash import MuseDashTracker
from .osu import OsuTracker
from .slocklock import SlotLock

TRACKERS: dict[str, CustomTracker] = {
    "Muse Dash": MuseDashTracker(),
    "osu!": OsuTracker(),
    "SlotLock": SlotLock(),
}
