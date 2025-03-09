import os
import subprocess

if not os.path.exists("world_data"):
    subprocess.run(["git", "clone", "git@github.com:silasary/world_data.git"])

from discordbot import main

main.init()
