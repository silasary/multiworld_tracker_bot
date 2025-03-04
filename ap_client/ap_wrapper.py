import os
import subprocess
import sys


def clone_repo() -> None:
    # repo_url = "https://github.com/ArchipelagoMW/Archipelago.git"
    repo_url = "https://github.com/FarisTheAncient/Archipelago.git"
    if os.path.exists("Archipelago"):
        subprocess.run(["git", "reset", "--hard", "origin/tracker"], cwd="Archipelago")
        subprocess.run(["git", "pull"], cwd="Archipelago")
    else:
        subprocess.run(["git", "clone", repo_url, "Archipelago"])
        subprocess.run(["git", "checkout", "tracker"], cwd="Archipelago")


clone_repo()

sys.path.append("Archipelago")
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Utils  # noqa

Utils.local_path.cached_path = os.path.dirname(os.path.abspath(Utils.__file__))

import ModuleUpdate  # noqa

ModuleUpdate.update(yes=True)
from Archipelago.worlds.tracker.TrackerClient import TrackerGameContext, updateTracker  # noqa


def get_tracker_ctx(name):
    ctx = TrackerGameContext("", "", no_connection=True)
    ctx.run_generator()

    ctx.player_id = ctx.launch_multiworld.world_name_lookup[name]
    return ctx


def get_in_logic(ctx, items=[], locations=[]):
    ctx.items_received = [(item,) for item in items]  # to account for the list being ids and not Items
    ctx.missing_locations = locations
    updateTracker(ctx)
    return ctx.locations_available
