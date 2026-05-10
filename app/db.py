from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from .config import DATABASE_URL

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    from . import models  # noqa: F401  ensure models are registered

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight upgrades for older databases.
        try:
            await conn.exec_driver_sql(
                "ALTER TABLE sounds ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass
