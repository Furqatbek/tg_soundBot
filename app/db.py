from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from .config import DATABASE_URL

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# DDL we run against an existing database to bring it forward. ALTER TABLE
# ADD COLUMN is the only schema change SQLite supports without a rebuild,
# which is fine for what we need. Each statement is wrapped in try/except
# at runtime so re-running on an already-migrated DB is a no-op.
MIGRATIONS = [
    "ALTER TABLE sounds ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE sounds ADD COLUMN pack_id INTEGER",
]

# FTS5 virtual table mirroring (name, tags) from `sounds`. We use external
# content (`content='sounds'`) so the FTS index doesn't duplicate the data,
# and triggers keep it in sync on every write to `sounds`. Searching is
# `WHERE sounds_fts MATCH :q` and ranking comes from `ORDER BY rank`.
FTS_SETUP = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS sounds_fts USING fts5("
    "name, tags, content='sounds', content_rowid='id')",
    "CREATE TRIGGER IF NOT EXISTS sounds_ai AFTER INSERT ON sounds BEGIN "
    "INSERT INTO sounds_fts(rowid, name, tags) "
    "VALUES (new.id, new.name, new.tags); END",
    "CREATE TRIGGER IF NOT EXISTS sounds_ad AFTER DELETE ON sounds BEGIN "
    "INSERT INTO sounds_fts(sounds_fts, rowid, name, tags) "
    "VALUES ('delete', old.id, old.name, old.tags); END",
    "CREATE TRIGGER IF NOT EXISTS sounds_au AFTER UPDATE ON sounds BEGIN "
    "INSERT INTO sounds_fts(sounds_fts, rowid, name, tags) "
    "VALUES ('delete', old.id, old.name, old.tags); "
    "INSERT INTO sounds_fts(rowid, name, tags) "
    "VALUES (new.id, new.name, new.tags); END",
]


async def init_db() -> None:
    from . import models  # noqa: F401  ensure models are registered

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in MIGRATIONS:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                pass
        for ddl in FTS_SETUP:
            await conn.exec_driver_sql(ddl)
        # Backfill the FTS index from any pre-existing rows.
        await conn.exec_driver_sql(
            "INSERT INTO sounds_fts(rowid, name, tags) "
            "SELECT id, name, tags FROM sounds "
            "WHERE id NOT IN (SELECT rowid FROM sounds_fts)"
        )
