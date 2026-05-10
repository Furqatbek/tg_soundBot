"""Verify the SQLite pragmas (WAL etc.) are set on every connection."""

from __future__ import annotations

from app.db import SessionLocal


async def _pragma(name: str):
    async with SessionLocal() as session:
        conn = await session.connection()
        result = await conn.exec_driver_sql(f"PRAGMA {name}")
        return result.scalar()


async def test_wal_mode_enabled():
    assert (await _pragma("journal_mode") or "").lower() == "wal"


async def test_foreign_keys_enabled():
    assert await _pragma("foreign_keys") == 1


async def test_synchronous_normal():
    # 1 == NORMAL
    assert await _pragma("synchronous") == 1
