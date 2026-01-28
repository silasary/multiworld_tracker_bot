# Archipelago Alert Bot

Simple discord bot.  It watches your slots in asyncs, and sends Discord DMs when you get stuff.


## How to use

1. Authorize the bot here: https://discord.com/oauth2/authorize?client_id=1285486268876062741
2. Click "Try it Now" for personal use, or "Add to Server" if you have a dedicated server for your async
3. In Discord, type `/ap track`, and give it either:
  - A slot tracker: https://archipelago.gg/tracker/AAAAAAAAAAAAAA/0/7
  - A room tracker: https://archipelago.gg/tracker/AAAAAAAAAAAAAA (If you've claimed slots on the corresponding cheese tracker)
  - A Cheese Tracker URL: https://cheesetrackers.theincrediblewheelofchee.se/tracker/AAAAAAAAAAAAAA (If you've claimed slots)
4. Wait for hourly updates 😄

![image](https://github.com/user-attachments/assets/6e37d4e7-8562-4b8f-94e0-7d3d21963281)

## Self-hosting

1. Create a new application at <https://discordapp.com/developers/applications/me>
2. In the installation tab:
  * Enable "User Install"
  * Set "Install Link" to Disord Provided Link
    * Authorize the bot to your account using the provided link
  * Set default install settings to `applications.commands` for user install, and `applications.commands`, `bot` for Guild Install
3. Under the bot tab, create a bot and note down its token.
4. Clone the repo
5. Create a .env file, and set `token=XXXXXXXXXXXXXXXXXX.XXXXXXXXXXX`
6. Use your preferred daemon manager to run `bash run.sh` (see below)
7. Go to step 3 of "How to Use"

### Example systemd service

```
[Service]
ExecStart=/bin/bash /home/silasary/repos/multiworld_tracker_bot/run.sh
User=silasary
Restart=always
[Install]
WantedBy=multi-user.target
```
