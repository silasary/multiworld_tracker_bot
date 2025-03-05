"""
Placeholder system for item classification.

Temporary until https://github.com/ArchipelagoMW/Archipelago/pull/1052 gets merged.
"""
import logging
import os
import asyncio
import subprocess
import sys

from interactions.models.internal.tasks import IntervalTrigger, Task

from .multiworld import Datapackage, ItemClassification, DATAPACKAGES

classifications = {v.name: v for v in ItemClassification}


async def git(args: list[str], cwd: str) -> None:
    """Run a git command."""
    logging.info(f"Running git {' '.join(args)} in {cwd}")
    if sys.platform == "win32":
        subprocess.run(["git", *args], cwd=cwd)
    else:
        process = await asyncio.subprocess.create_subprocess_exec("git", *args, cwd=cwd)
        await process.wait()


@Task.create(IntervalTrigger(days=1))
async def update_datapackage() -> None:
    """Update the datapackage."""
    await clone_repo()
    await update_all(DATAPACKAGES)


async def clone_repo() -> None:
    repo_url = "git@github.com:silasary/world_data.git"
    if os.path.exists("world_data"):
        await git(["clean", "-fdx"], cwd="world_data")
        await git(["pull"], cwd="world_data")
        # await git(["reset", "--hard "origin/main"], cwd="world_data")

    else:
        await git(["clone", repo_url, "world_data"])


async def update_all(dps: dict[str, Datapackage]) -> None:
    """Update all datapackages."""
    for name, dp in dps.items():
        await import_datapackage(name, dp)


async def import_datapackage(name: str, dp: Datapackage) -> None:
    logging.info(f"Loading datapackage {name}")
    if name is None:
        return
    if name in ["None", "null"]:
        return

    DATAPACKAGES[name] = dp
    written = False
    if not os.path.exists("world_data"):
        await clone_repo()
    if not os.path.exists(os.path.join("world_data", "worlds", name, "progression.txt")):
        logging.info(f"Datapackage {name} not found in Zoggoth's repo.")
        os.makedirs(os.path.join("world_data", "worlds", name), exist_ok=True)
        with open(os.path.join("world_data", "worlds", name, "progression.txt"), "w") as f:
            f.write("")
            written = True

    set(dp.items.keys())
    to_append = set(k for k, v in dp.items.items() if v not in [ItemClassification.unknown, ItemClassification.bad_name])
    to_append.discard("Rollback detected!")
    to_replace = set()

    trailing_newline = True

    with open(os.path.join("world_data", "worlds", name, "progression.txt")) as f:
        for line in f:
            if not line.strip():
                trailing_newline = True
                continue
            splits = line.split(": ")
            key = ": ".join(splits[:-1])
            value = splits[-1].strip().lower()

            to_append.discard(key)
            if value == "unknown":
                # logging.info(f"Zoggoth doesn't know the classification for item {key} in {name}.")
                if key in dp.items:
                    to_replace.add(key)
                continue
            elif value in classifications:
                dp.items[key] = classifications[value]
            else:
                logging.error(f"Unknown classification `{value}` for item {key} in {name}.")
            trailing_newline = line.endswith("\n")

    # written = False
    if to_replace:
        with open(os.path.join("world_data", "worlds", name, "progression.txt"), "r") as f:
            lines = f.readlines()
        with open(os.path.join("world_data", "worlds", name, "progression.txt"), "w") as f:
            for line in lines:
                if not line.strip():
                    f.write("\n")
                    continue
                splits = line.split(": ")
                key = ": ".join(splits[:-1])
                if key in to_replace:
                    v = dp.items[key]
                    f.write(f"{key}: {v.name}\n")
                else:
                    f.write(line)
            written = True
    if to_append:
        with open(os.path.join("world_data", "worlds", name, "progression.txt"), "a") as f:
            if not trailing_newline:
                f.write("\n")
            for item in to_append:
                v = dp.items[item]
                if v == ItemClassification.bad_name:
                    continue
                f.write(f"{item}: {v.name}\n")
                written = True

    if written:
        to_append = to_append | to_replace
        await git(["add", f"worlds/{name}/progression.txt"], cwd="world_data")
        if not to_append:
            message = f"{name}: Create progression.txt"
        elif len(to_append) < 4:
            message = f"{name}: Add {', '.join(to_append)}"
        else:
            message = f"{name}: Add {len(to_append)} items"
        await git(["commit", "-m", message], cwd="world_data")
        await git(["push", "-u", "git@github.com:silasary/world_data.git"], cwd="world_data")
        # await git(["checkout", "main"], cwd="world_data")
