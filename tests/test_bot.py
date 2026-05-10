"""Unit tests for the inline + admin-upload Telegram handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram import (
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedVoice,
)

from app import bot as bot_module
from app.db import SessionLocal
from app.models import Sound


async def _seed(*sounds: Sound) -> None:
    async with SessionLocal() as session:
        for s in sounds:
            session.add(s)
        await session.commit()


def _make_inline_update(query: str):
    inline_query = MagicMock()
    inline_query.query = query
    inline_query.answer = AsyncMock()
    update = MagicMock()
    update.inline_query = inline_query
    return update, inline_query


# ---------------------------------------------------------------------------
# handle_inline
# ---------------------------------------------------------------------------


async def test_inline_empty_query_returns_all_recent():
    await _seed(
        Sound(name="alpha", tags="", file_id="f1", file_unique_id="u1", kind="voice"),
        Sound(name="beta", tags="meme", file_id="f2", file_unique_id="u2", kind="audio"),
    )

    update, iq = _make_inline_update("")
    await bot_module.handle_inline(update, MagicMock())

    iq.answer.assert_awaited_once()
    results = iq.answer.await_args.args[0]
    assert len(results) == 2


async def test_inline_filters_by_name():
    await _seed(
        Sound(name="bruh", tags="reaction", file_id="f1", file_unique_id="u1", kind="voice"),
        Sound(name="laser", tags="sci-fi", file_id="f2", file_unique_id="u2", kind="audio"),
    )

    update, iq = _make_inline_update("bruh")
    await bot_module.handle_inline(update, MagicMock())

    results = iq.answer.await_args.args[0]
    assert len(results) == 1
    assert isinstance(results[0], InlineQueryResultCachedVoice)
    assert results[0].voice_file_id == "f1"


async def test_inline_filters_by_tags():
    await _seed(
        Sound(name="bruh", tags="reaction", file_id="f1", file_unique_id="u1", kind="voice"),
        Sound(name="laser", tags="sci-fi", file_id="f2", file_unique_id="u2", kind="audio"),
    )

    update, iq = _make_inline_update("sci")
    await bot_module.handle_inline(update, MagicMock())

    results = iq.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].audio_file_id == "f2"


async def test_inline_picks_correct_result_class_per_kind():
    await _seed(
        Sound(name="vc", tags="", file_id="vfid", file_unique_id="vu", kind="voice"),
        Sound(name="au", tags="", file_id="afid", file_unique_id="au", kind="audio"),
        Sound(name="dc", tags="", file_id="dfid", file_unique_id="du", kind="document"),
    )

    update, iq = _make_inline_update("")
    await bot_module.handle_inline(update, MagicMock())

    results = iq.answer.await_args.args[0]
    by_class = {type(r) for r in results}
    assert InlineQueryResultCachedVoice in by_class
    assert InlineQueryResultCachedAudio in by_class
    assert InlineQueryResultCachedDocument in by_class


async def test_inline_no_results():
    update, iq = _make_inline_update("nothing")
    await bot_module.handle_inline(update, MagicMock())
    assert iq.answer.await_args.args[0] == []


# ---------------------------------------------------------------------------
# handle_admin_upload
# ---------------------------------------------------------------------------


def _admin_update(*, caption=None, voice=None, audio=None, document=None, user_id=111):
    msg = MagicMock()
    msg.caption = caption
    msg.voice = voice
    msg.audio = audio
    msg.document = document
    msg.reply_text = AsyncMock()

    update = MagicMock()
    update.message = msg
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update, msg


async def _all_sounds():
    async with SessionLocal() as session:
        return (await session.execute(select(Sound))).scalars().all()


async def test_admin_upload_ignores_non_admin():
    voice = MagicMock(file_id="x", file_unique_id="xu", mime_type="audio/ogg", duration=1)
    update, msg = _admin_update(caption="name", voice=voice, user_id=999)

    await bot_module.handle_admin_upload(update, MagicMock())

    msg.reply_text.assert_not_awaited()
    assert await _all_sounds() == []


async def test_admin_upload_requires_caption():
    voice = MagicMock(file_id="x", file_unique_id="xu", mime_type="audio/ogg", duration=1)
    update, msg = _admin_update(caption=None, voice=voice)

    await bot_module.handle_admin_upload(update, MagicMock())

    msg.reply_text.assert_awaited_once()
    assert await _all_sounds() == []


async def test_admin_upload_skips_self_uploaded_marker():
    voice = MagicMock(file_id="x", file_unique_id="xu", mime_type="audio/ogg", duration=1)
    update, msg = _admin_update(caption="[uploaded] hello", voice=voice)

    await bot_module.handle_admin_upload(update, MagicMock())

    msg.reply_text.assert_not_awaited()
    assert await _all_sounds() == []


async def test_admin_upload_saves_voice_with_tags():
    voice = MagicMock(file_id="vfid", file_unique_id="vu", mime_type="audio/ogg", duration=3)
    update, msg = _admin_update(caption="bruh | meme, fail", voice=voice)

    await bot_module.handle_admin_upload(update, MagicMock())

    msg.reply_text.assert_awaited_once()
    rows = await _all_sounds()
    assert len(rows) == 1
    snd = rows[0]
    assert snd.name == "bruh"
    assert snd.tags == "meme, fail"
    assert snd.kind == "voice"
    assert snd.file_id == "vfid"
    assert snd.duration == 3


async def test_admin_upload_saves_audio_without_tags():
    audio = MagicMock(file_id="afid", file_unique_id="au", mime_type="audio/mpeg", duration=10)
    update, msg = _admin_update(caption="song", audio=audio)

    await bot_module.handle_admin_upload(update, MagicMock())

    rows = await _all_sounds()
    assert len(rows) == 1
    assert rows[0].kind == "audio"
    assert rows[0].name == "song"
    assert rows[0].tags == ""


async def test_admin_upload_empty_name_rejected():
    voice = MagicMock(file_id="x", file_unique_id="xu", mime_type="audio/ogg", duration=1)
    update, msg = _admin_update(caption="  | onlytags", voice=voice)

    await bot_module.handle_admin_upload(update, MagicMock())

    msg.reply_text.assert_awaited_once()
    assert await _all_sounds() == []


@pytest.mark.parametrize(
    "kind,attrs",
    [
        ("voice", dict(voice=MagicMock(file_id="vfid", file_unique_id="vu", mime_type="audio/ogg", duration=1))),
        ("audio", dict(audio=MagicMock(file_id="afid", file_unique_id="au", mime_type="audio/mpeg", duration=2))),
        ("document", dict(document=MagicMock(file_id="dfid", file_unique_id="du", mime_type="application/pdf", duration=None))),
    ],
)
async def test_admin_upload_each_media_kind(kind, attrs):
    update, msg = _admin_update(caption=f"thing | t1", **attrs)

    await bot_module.handle_admin_upload(update, MagicMock())

    rows = await _all_sounds()
    assert len(rows) == 1
    assert rows[0].kind == kind
