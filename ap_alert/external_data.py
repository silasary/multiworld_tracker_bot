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


async def git(args: list[str], cwd: str) -> int:
    """Run a git command."""
    print(f"Running git {' '.join(args)} in {cwd}")
    if sys.platform == "win32":
        try:
            subprocess.run(["git", *args], cwd=cwd, check=True)
            return 0
        except subprocess.CalledProcessError as e:
            return e.returncode
    else:
        process = await asyncio.subprocess.create_subprocess_exec("git", *args, cwd=cwd)
        code = await process.wait()
        return code


async def git_output(args: list[str], cwd: str) -> str:
    """Run a git command."""
    logging.info(f"Running git {' '.join(args)} in {cwd}")
    if sys.platform == "win32":
        try:
            return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
        except subprocess.CalledProcessError as e:
            return e.returncode
    else:
        process = await asyncio.subprocess.create_subprocess_exec("git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE)
        output = await process.communicate()
        return output[0].decode("utf-8")


@Task.create(IntervalTrigger(hours=1))
async def update_datapackage() -> None:
    """Update the datapackage."""
    await clone_repo()
    await update_all(DATAPACKAGES)


async def clone_repo() -> None:
    repo_url = "git@github.com:silasary/world_data.git"
    if os.path.exists("world_data"):
        await git(["clean", "-fdx"], cwd="world_data")
        await git(["pull", "--commit", "origin", "main"], cwd="world_data")
        # await git(["reset", "--hard "origin/main"], cwd="world_data")

    else:
        await git(["clone", repo_url, "world_data"])


async def update_all(dps: dict[str, Datapackage]) -> None:
    """Update all datapackages."""
    for name, dp in dps.copy().items():
        await import_datapackage(name, dp)


async def load_all(dps: dict[str, Datapackage]) -> None:
    """Load all datapackages."""
    await clone_repo()
    for name, dp in dps.items():
        await import_datapackage(name, dp)


async def import_datapackage(name: str, dp: Datapackage) -> Datapackage:
    logging.info(f"Loading datapackage {name}")
    if name is None:
        return
    if name in ["None", "null"]:
        return

    if not os.path.exists("world_data"):
        await clone_repo()
    from world_data.models import load_datapackage, save_datapackage

    safe_name = name.replace("/", "_").replace(":", "_")
    dp = load_datapackage(safe_name, dp)
    DATAPACKAGES[name] = dp

    save_datapackage(name, dp)
    name = getattr(dp, "game_name", None) or name
    await push(name)

    return dp


async def push(name: str) -> None:
    safe_name = name.replace("/", "_").replace(":", "_")
    output = await git_output(["diff", "--numstat"], cwd="world_data")
    if output.strip():
        raw_lines_added = sum(int(x.split("\t")[0]) for x in output.splitlines())
        raw_lines_removed = sum(int(x.split("\t")[1]) for x in output.splitlines())
        lines_added = raw_lines_added - raw_lines_removed
        lines_updated = raw_lines_added - lines_added

        await git(["add", f"worlds/{safe_name}/progression.txt"], cwd="world_data")
        message = f"{name}:"
        if lines_added > 0:
            message += f" {lines_added} items added"
        if lines_updated > 0:
            message += f" {lines_updated} items updated"
        await git(["commit", "-m", message], cwd="world_data")
        await git(["push", "git@github.com:silasary/world_data.git"], cwd="world_data")
