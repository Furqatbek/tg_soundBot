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

    import os as _os

    from .config import DATABASE_URL, UPLOAD_DIR

    in_docker = _os.path.exists("/.dockerenv")
    logger = logging.getLogger("app")
    logger.info("DATABASE_URL=%s", DATABASE_URL)
    logger.info("UPLOAD_DIR=%s", UPLOAD_DIR)
    if in_docker:
        if "/data/" not in DATABASE_URL and not DATABASE_URL.startswith("postgres"):
            logger.warning(
                "DATABASE_URL is not under /data inside this container. "
                "Data will be lost on `docker compose down`. Remove "
                "DATABASE_URL from .env to fall back to the Dockerfile "
                "default (/data/sounds.db)."
            )
        if not UPLOAD_DIR.startswith("/data"):
            logger.warning(
                "UPLOAD_DIR is not under /data inside this container. "
                "Uploaded files will be lost on `docker compose down`. "
                "Remove UPLOAD_DIR from .env to fall back to the "
                "Dockerfile default (/data/uploads)."
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
