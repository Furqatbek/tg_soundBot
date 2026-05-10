from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from .config import DATABASE_URL

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def attach_sqlite_pragmas(target_engine) -> None:
    """Set WAL + sane defaults on every new SQLite connection.

    No-op for non-SQLite engines, so swapping in Postgres later just
    skips this.
    """

    @event.listens_for(target_engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _):
        if target_engine.url.get_backend_name() != "sqlite":
            return
        cur = dbapi_conn.cursor()
        # WAL = readers don't block writers and vice versa. ~5-10x more
        # write throughput than the default rollback journal.
        cur.execute("PRAGMA journal_mode=WAL")
        # synchronous=NORMAL is safe with WAL (only risk is losing the
        # very last in-flight commit on a hard power loss, never corruption).
        cur.execute("PRAGMA synchronous=NORMAL")
        # FK constraints are off by default in SQLite; turn them on so
        # ForeignKey() in the models actually does something.
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


attach_sqlite_pragmas(engine)


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
