import asyncio
import sys
from redis import asyncio as aioredis
import interactions
from interactions.ext import prefixed_commands as prefixed
from interactions.ext import hybrid_commands as hybrid

from shared import configuration

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class Bot(interactions.Client):
    def __init__(self) -> None:
        super().__init__(
            intents=interactions.Intents.DEFAULT,
            enable_emoji_cache=True,
            sync_interactions=True,
            sync_ext=True,
            send_command_tracebacks=False,
        )
        super().load_extension(
            "interactions.ext.sentry",
            dsn="https://7aadf0c15f880e90e01c4dba496f152d@o233010.ingest.us.sentry.io/4507219660832768",
            enable_tracing=True,
        )
        super().load_extension("ap_alert")
        super().load_extension("interactions.ext.jurigged")

    def init(self) -> None:
        prefixed.setup(self)
        hybrid.setup(self)
        self.start(configuration.get("token"))

    async def on_ready(self) -> None:
        self.redis = await aioredis.create_redis_pool(
            "redis://localhost", minsize=5, maxsize=10
        )
        print(
            "Logged in as {username} ({id})".format(
                username=self.user.name, id=self.user.id
            )
        )
        print(
            "Connected to {0}".format(
                ", ".join([server.name for server in self.guilds])
            )
        )
        print("--------")


def init() -> None:
    client = Bot()
    client.init()


if __name__ == "__main__":
    init()
