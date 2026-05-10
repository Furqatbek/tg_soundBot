import asyncio
import logging

import uvicorn

from .bot import build_application
from .config import PORT
from .web import create_app


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bot_app = build_application()
    web_app = create_app(bot_app)

    config = uvicorn.Config(web_app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    try:
        await server.serve()
    finally:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
