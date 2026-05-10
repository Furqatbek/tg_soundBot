"""Integration tests for the FastAPI control panel.

Uses FastAPI's TestClient (which runs lifespan), the FakeBot from
conftest in place of a real Telegram bot, and a temporary SQLite DB +
uploads dir reset between tests.
"""

from __future__ import annotations

import os
import re

import pytest

from tests.conftest import login


def _ids_from_html(html: str) -> list[int]:
    return [int(m) for m in re.findall(r"/sounds/(\d+)/delete", html)]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_root_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (303, 307)
    assert "/login" in r.headers["location"]


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_login_bad_password(client):
    r = client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert "Invalid credentials" in r.text


def test_login_success_grants_access(client):
    login(client)
    r = client.get("/")
    assert r.status_code == 200
    assert "Upload" in r.text


def test_logout_clears_session(client):
    login(client)
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/", follow_redirects=False)
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_audio_persists_to_db_and_disk(client, fake_bot_app, upload_dir):
    login(client)

    r = client.post(
        "/sounds",
        data={"name": "hello", "tags": "greet,wave"},
        files={"file": ("hello.mp3", b"FAKE_MP3", "audio/mpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    # Disk: uses Telegram's file_unique_id + extension
    assert os.path.exists(os.path.join(upload_dir, "aunique.mp3"))

    # Bot received the right call (audio, with title, to admin chat 111)
    audio_calls = [c for c in fake_bot_app.bot.calls if c[0] == "audio"]
    assert len(audio_calls) == 1
    assert audio_calls[0][1] == 111
    assert audio_calls[0][3] == "hello"  # title

    # Listing reflects it
    r = client.get("/")
    assert "hello" in r.text


def test_upload_ogg_routed_as_voice(client, fake_bot_app, upload_dir):
    login(client)
    client.post(
        "/sounds",
        data={"name": "v", "tags": ""},
        files={"file": ("v.ogg", b"OGG", "audio/ogg")},
    )

    assert any(c[0] == "voice" for c in fake_bot_app.bot.calls)
    assert os.path.exists(os.path.join(upload_dir, "vunique.ogg"))


def test_upload_unknown_mime_routed_as_document(client, fake_bot_app, upload_dir):
    login(client)
    client.post(
        "/sounds",
        data={"name": "d", "tags": ""},
        files={"file": ("blob.bin", b"X", "application/octet-stream")},
    )

    assert any(c[0] == "document" for c in fake_bot_app.bot.calls)
    assert os.path.exists(os.path.join(upload_dir, "dunique.bin"))


def test_upload_requires_login(client):
    r = client.post(
        "/sounds",
        data={"name": "x", "tags": ""},
        files={"file": ("x.mp3", b"X", "audio/mpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_filters_by_name_and_tags(client):
    login(client)
    client.post(
        "/sounds",
        data={"name": "alpha", "tags": "first"},
        files={"file": ("a.mp3", b"X", "audio/mpeg")},
    )
    client.post(
        "/sounds",
        data={"name": "beta", "tags": "second"},
        files={"file": ("b.mp3", b"X", "audio/mpeg")},
    )

    r = client.get("/?q=alpha")
    assert "alpha" in r.text
    assert "beta" not in r.text

    r = client.get("/?q=second")
    assert "beta" in r.text
    assert "alpha" not in r.text


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def test_edit_renames_and_retags(client):
    login(client)
    client.post(
        "/sounds",
        data={"name": "old", "tags": "tag1"},
        files={"file": ("a.mp3", b"X", "audio/mpeg")},
    )
    [sid] = _ids_from_html(client.get("/").text)

    r = client.post(
        f"/sounds/{sid}/edit",
        data={"name": "new", "tags": "tag2"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    body = client.get("/").text
    assert "new" in body
    assert "tag2" in body
    assert ">old<" not in body


def test_edit_requires_login(client):
    # Log in, create one, log out, then try editing.
    login(client)
    client.post(
        "/sounds",
        data={"name": "x", "tags": ""},
        files={"file": ("a.mp3", b"X", "audio/mpeg")},
    )
    [sid] = _ids_from_html(client.get("/").text)
    client.post("/logout")

    r = client.post(
        f"/sounds/{sid}/edit",
        data={"name": "hax", "tags": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_row_and_disk_file(client, upload_dir):
    login(client)
    client.post(
        "/sounds",
        data={"name": "todel", "tags": ""},
        files={"file": ("a.mp3", b"X", "audio/mpeg")},
    )
    on_disk = os.path.join(upload_dir, "aunique.mp3")
    assert os.path.exists(on_disk)
    [sid] = _ids_from_html(client.get("/").text)

    r = client.post(f"/sounds/{sid}/delete", follow_redirects=False)
    assert r.status_code == 303

    assert not os.path.exists(on_disk)
    body = client.get("/").text
    assert "todel" not in body


def test_delete_missing_disk_file_is_tolerated(client, upload_dir):
    login(client)
    client.post(
        "/sounds",
        data={"name": "x", "tags": ""},
        files={"file": ("a.mp3", b"X", "audio/mpeg")},
    )
    on_disk = os.path.join(upload_dir, "aunique.mp3")
    os.remove(on_disk)  # simulate missing backup
    [sid] = _ids_from_html(client.get("/").text)

    r = client.post(f"/sounds/{sid}/delete", follow_redirects=False)
    assert r.status_code == 303  # no 500
