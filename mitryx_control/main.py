from __future__ import annotations

import asyncio
import logging

import uvicorn

from .api import create_api
from .bot import MitryxBot, register_commands
from .config import load_settings
from .database import Database


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = load_settings()
    database = Database(settings.database_path)
    await database.initialize()

    bot = MitryxBot(settings, database)
    register_commands(bot)
    api = create_api(settings, database, bot)
    uvicorn_config = uvicorn.Config(
        api,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        proxy_headers=settings.trust_proxy_headers,
        forwarded_allow_ips="*" if settings.trust_proxy_headers else "",
    )
    server = uvicorn.Server(uvicorn_config)

    api_task = asyncio.create_task(server.serve(), name="mitryx-api")
    bot_task = asyncio.create_task(bot.start(settings.discord_token), name="mitryx-bot")
    tasks = {api_task, bot_task}

    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                continue
            exception = task.exception()
            if exception:
                raise exception
    finally:
        server.should_exit = True
        await bot.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await database.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
