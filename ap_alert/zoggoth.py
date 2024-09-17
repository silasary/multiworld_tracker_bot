import os
import subprocess
import logging

from interactions.models.internal.tasks import Task, IntervalTrigger

from .multiworld import Datapackage, ItemClassification

classifications = {v.name: v for v in ItemClassification}

@Task.create(IntervalTrigger(days=1))
async def update_datapackage() -> None:
    """Update the datapackage."""
    clone_repo()


def clone_repo() -> None:
    repo_url = "https://github.com/Zoggoth/Zoggoths-Archipelago-Multitracker.git"
    if os.path.exists("zoggoth_repo"):
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd="zoggoth_repo")
    else:
        subprocess.run(["git", "clone", repo_url, "zoggoth_repo"])

def update_all(dps: dict[str, Datapackage]) -> None:
    """Update all datapackages."""
    for name, dp in dps.items():
        load_datapackage(name, dp)

def load_datapackage(name: str, dp: Datapackage) -> None:
    if not os.path.exists("zoggoth_repo"):
        clone_repo()
    if not os.path.exists(os.path.join("zoggoth_repo", "worlds", name, "progression.txt")):
        logging.info(f"Datapackage {name} not found in Zoggoth's repo.")
        os.makedirs(os.path.join("zoggoth_repo", "worlds", name), exist_ok=True)
        with open(os.path.join("zoggoth_repo", "worlds", name, "progression.txt"), "w") as f:
            f.write("")

    to_append = set(dp.items.keys())
    to_append.discard("Rollback detected!")

    trailing_newline = False

    with open(os.path.join("zoggoth_repo", "worlds", name, "progression.txt")) as f:
        for line in f:
            key, value = line.split(": ")
            value = value.strip().lower()
            to_append.discard(key)
            if value == "unknown":
                logging.info(f"Zoggoth doesn't know the classification for item {key} in {name}.")
                continue
            elif value in classifications:
                dp.items[key] = classifications[value]
            else:
                logging.error(f"Unknown classification `{value}` for item {key} in {name}.")
            trailing_newline = line.endswith("\n")

    if to_append:
        with open(os.path.join("zoggoth_repo", "worlds", name, "progression.txt"), "a") as f:
            if not trailing_newline:
                f.write("\n")
            for item in to_append:
                v = repr(dp.items[item]).split(".")[1].split(':')[0]
                if v == "unknown":
                    continue
                f.write(f"{item}: {v}\n")
