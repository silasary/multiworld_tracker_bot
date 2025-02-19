import os
import subprocess
import sys


def clone_repo() -> None:
    repo_url = "https://github.com/ArchipelagoMW/Archipelago.git"
    if os.path.exists("Archipelago"):
        subprocess.run(["git", "reset", "--hard", "origin/main"], cwd="Archipelago")
        subprocess.run(["git", "pull"], cwd="Archipelago")
    else:
        subprocess.run(["git", "clone", repo_url, "Archipelago"])

clone_repo()

sys.path.append("Archipelago")
if __name__ == "__main__":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Utils  # noqa

Utils.local_path.cached_path = os.path.dirname(os.path.abspath(Utils.__file__))

import ModuleUpdate

ModuleUpdate.update(yes=True)
from Archipelago import CommonClient
