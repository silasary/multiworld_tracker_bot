from .muse_dash import MuseDashTracker
from .osu import OsuTracker

TRACKERS = {
    "Muse Dash": MuseDashTracker(),
    "osu!": OsuTracker(),
}
