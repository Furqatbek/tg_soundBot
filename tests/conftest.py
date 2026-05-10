"""Test fixtures.

Configures env BEFORE any app modules are imported, then swaps the async
engine to use NullPool so connections aren't held across event loops
(pytest-asyncio creates a new loop per test).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMPROOT = Path(tempfile.mkdtemp(prefix="tg_soundbot_test_"))
_DB_PATH = _TMPROOT / "test.db"
_UPLOAD_DIR = _TMPROOT / "uploads"

os.environ["BOT_TOKEN"] = "test:token"
os.environ["ADMIN_CHAT_ID"] = "111"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret"
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"
os.environ["UPLOAD_DIR"] = str(_UPLOAD_DIR)

# Now safe to import app code.
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

import app.bot as bot_module  # noqa: E402
import app.db as db_module  # noqa: E402
import app.web as web_module  # noqa: E402

# Replace pooled engine with NullPool so connections never outlive a request.
db_module.engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
db_module.SessionLocal = async_sessionmaker(
    db_module.engine, class_=AsyncSession, expire_on_commit=False
)
# Modules captured the original SessionLocal at import time; rebind them.
bot_module.SessionLocal = db_module.SessionLocal
web_module.SessionLocal = db_module.SessionLocal


@pytest.fixture(autouse=True)
def _reset_state():
    """Truncate tables, set up FTS5, and wipe uploads dir before every test."""
    from app.db import FTS_SETUP, Base

    sync_url = os.environ["DATABASE_URL"].replace("sqlite+aiosqlite", "sqlite")
    eng = create_engine(sync_url)
    Base.metadata.create_all(eng)  # idempotent
    with eng.begin() as conn:
        for ddl in FTS_SETUP:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql("DELETE FROM play_events")
        conn.exec_driver_sql("DELETE FROM sounds")
        conn.exec_driver_sql("DELETE FROM packs")
        conn.exec_driver_sql("DELETE FROM users")
    eng.dispose()

    shutil.rmtree(_UPLOAD_DIR, ignore_errors=True)
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def upload_dir() -> str:
    return str(_UPLOAD_DIR)


# ---------------------------------------------------------------------------
# Fake Telegram bot
# ---------------------------------------------------------------------------


class _FakeMedia:
    def __init__(self, file_id, file_unique_id, mime_type=None, duration=None):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.mime_type = mime_type
        self.duration = duration


class _FakeSent:
    def __init__(self, kind, media):
        self.voice = media if kind == "voice" else None
        self.audio = media if kind == "audio" else None
        self.document = media if kind == "document" else None


class FakeBot:
    """Minimal stand-in for telegram.Bot that records calls."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def send_voice(self, chat_id, voice, caption=None, **_):
        self.calls.append(("voice", chat_id, caption))
        return _FakeSent(
            "voice",
            _FakeMedia("vfid", "vunique", mime_type="audio/ogg", duration=3),
        )

    async def send_audio(self, chat_id, audio, caption=None, title=None, **_):
        self.calls.append(("audio", chat_id, caption, title))
        return _FakeSent(
            "audio",
            _FakeMedia("afid", "aunique", mime_type="audio/mpeg", duration=5),
        )

    async def send_document(self, chat_id, document, caption=None, **_):
        self.calls.append(("document", chat_id, caption))
        return _FakeSent(
            "document",
            _FakeMedia("dfid", "dunique", mime_type="application/octet-stream"),
        )

    async def get_me(self):
        class _Me:
            username = "fake_bot"

        return _Me()

    async def set_my_commands(self, commands):
        self.calls.append(("set_my_commands", [c.command for c in commands]))

    async def send_message(self, chat_id, text, **_):
        self.calls.append(("send_message", chat_id, text))


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()


@pytest.fixture
def fake_bot_app() -> FakeApp:
    return FakeApp()


@pytest.fixture
def client(fake_bot_app):
    """FastAPI TestClient with lifespan (creates tables + upload dir)."""
    from fastapi.testclient import TestClient

    from app.web import create_app

    app = create_app(fake_bot_app)
    with TestClient(app) as c:
        yield c


def login(client) -> None:
    r = client.post(
        "/login",
        data={"username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
