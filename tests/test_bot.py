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
from tests.conftest import FakeBot


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


# ---------------------------------------------------------------------------
# Popularity sort + chosen_inline_result
# ---------------------------------------------------------------------------


async def test_inline_orders_by_play_count_desc():
    await _seed(
        Sound(name="rare", tags="", file_id="f1", file_unique_id="u1", kind="audio", play_count=0),
        Sound(name="popular", tags="", file_id="f2", file_unique_id="u2", kind="audio", play_count=10),
        Sound(name="medium", tags="", file_id="f3", file_unique_id="u3", kind="audio", play_count=3),
    )

    update, iq = _make_inline_update("")
    await bot_module.handle_inline(update, MagicMock())
    results = iq.answer.await_args.args[0]
    assert [r.audio_file_id for r in results] == ["f2", "f3", "f1"]


async def test_inline_result_id_is_sound_id():
    await _seed(
        Sound(name="x", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    rows = await _all_sounds()
    expected_id = str(rows[0].id)

    update, iq = _make_inline_update("")
    await bot_module.handle_inline(update, MagicMock())
    results = iq.answer.await_args.args[0]
    assert results[0].id == expected_id


async def test_voice_title_includes_duration():
    await _seed(
        Sound(name="bruh", tags="", file_id="f1", file_unique_id="u1", kind="voice", duration=4),
        Sound(name="silent", tags="", file_id="f2", file_unique_id="u2", kind="voice"),
    )
    update, iq = _make_inline_update("")
    await bot_module.handle_inline(update, MagicMock())
    results = iq.answer.await_args.args[0]
    titles = {r.voice_file_id: r.title for r in results}
    assert titles["f1"] == "bruh (4s)"
    assert titles["f2"] == "silent"


async def test_document_uses_tags_as_description():
    await _seed(
        Sound(name="thing", tags="meme, fail", file_id="f1", file_unique_id="u1", kind="document"),
    )
    update, iq = _make_inline_update("")
    await bot_module.handle_inline(update, MagicMock())
    [doc] = iq.answer.await_args.args[0]
    assert doc.description == "meme, fail"


async def test_chosen_inline_result_bumps_play_count():
    await _seed(
        Sound(name="x", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    rows = await _all_sounds()
    sid = rows[0].id

    chosen = MagicMock()
    chosen.result_id = str(sid)
    update = MagicMock()
    update.chosen_inline_result = chosen

    await bot_module.handle_chosen(update, MagicMock())
    await bot_module.handle_chosen(update, MagicMock())

    rows = await _all_sounds()
    assert rows[0].play_count == 2


async def test_chosen_inline_result_ignores_non_numeric_id():
    await _seed(
        Sound(name="x", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    chosen = MagicMock()
    chosen.result_id = "not-a-number"
    update = MagicMock()
    update.chosen_inline_result = chosen

    await bot_module.handle_chosen(update, MagicMock())  # should not raise

    rows = await _all_sounds()
    assert rows[0].play_count == 0


# ---------------------------------------------------------------------------
# /help, /random, /list
# ---------------------------------------------------------------------------


def _ctx_with_fake_bot() -> tuple[MagicMock, FakeBot]:
    bot = FakeBot()
    ctx = MagicMock()
    ctx.bot = bot
    return ctx, bot


def _command_update(chat_id: int = 555):
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    update = MagicMock()
    update.message = msg
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, msg


async def test_cmd_help_replies_with_username():
    ctx, _ = _ctx_with_fake_bot()
    update, msg = _command_update()
    await bot_module.cmd_help(update, ctx)
    msg.reply_text.assert_awaited_once()
    text = msg.reply_text.await_args.args[0]
    assert "@fake_bot" in text
    assert "/random" in text


async def test_cmd_random_with_no_sounds():
    ctx, bot = _ctx_with_fake_bot()
    update, msg = _command_update()
    await bot_module.cmd_random(update, ctx)
    msg.reply_text.assert_awaited_once()
    assert bot.calls == []


async def test_cmd_random_sends_a_sound():
    await _seed(
        Sound(name="only", tags="", file_id="audfid", file_unique_id="u1", kind="audio"),
    )
    ctx, bot = _ctx_with_fake_bot()
    update, _ = _command_update(chat_id=777)

    await bot_module.cmd_random(update, ctx)

    audio_calls = [c for c in bot.calls if c[0] == "audio"]
    assert len(audio_calls) == 1
    assert audio_calls[0][1] == 777


async def test_cmd_list_orders_by_play_count():
    await _seed(
        Sound(name="rare", tags="", file_id="f1", file_unique_id="u1", kind="audio", play_count=1),
        Sound(name="popular", tags="", file_id="f2", file_unique_id="u2", kind="audio", play_count=20),
    )
    ctx, _ = _ctx_with_fake_bot()
    update, msg = _command_update()

    await bot_module.cmd_list(update, ctx)

    msg.reply_text.assert_awaited_once()
    text = msg.reply_text.await_args.args[0]
    pop_idx = text.index("popular")
    rare_idx = text.index("rare")
    assert pop_idx < rare_idx
    assert "20 plays" in text


async def test_cmd_list_with_no_sounds():
    ctx, _ = _ctx_with_fake_bot()
    update, msg = _command_update()
    await bot_module.cmd_list(update, ctx)
    msg.reply_text.assert_awaited_once()
    assert "No sounds" in msg.reply_text.await_args.args[0]


async def test_inline_fts_matches_prefix():
    await _seed(
        Sound(name="bruh", tags="", file_id="f1", file_unique_id="u1", kind="voice"),
        Sound(name="laser", tags="", file_id="f2", file_unique_id="u2", kind="audio"),
    )
    update, iq = _make_inline_update("bru")
    await bot_module.handle_inline(update, MagicMock())
    results = iq.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].voice_file_id == "f1"


async def test_inline_pack_filter():
    from app.models import Pack

    async with SessionLocal() as session:
        horns = Pack(name="Horns", slug="horns")
        memes = Pack(name="Memes", slug="memes")
        session.add_all([horns, memes])
        await session.commit()
        session.add_all([
            Sound(name="airhorn", tags="", file_id="f1", file_unique_id="u1",
                  kind="audio", pack_id=horns.id),
            Sound(name="bruh", tags="", file_id="f2", file_unique_id="u2",
                  kind="voice", pack_id=memes.id),
        ])
        await session.commit()

    update, iq = _make_inline_update("pack:horns")
    await bot_module.handle_inline(update, MagicMock())
    results = iq.answer.await_args.args[0]
    assert [r.audio_file_id for r in results] == ["f1"]


async def test_inline_pack_filter_with_text():
    from app.models import Pack

    async with SessionLocal() as session:
        horns = Pack(name="Horns", slug="horns")
        session.add(horns)
        await session.commit()
        session.add_all([
            Sound(name="airhorn", tags="loud", file_id="f1", file_unique_id="u1",
                  kind="audio", pack_id=horns.id),
            Sound(name="dinghorn", tags="soft", file_id="f2", file_unique_id="u2",
                  kind="audio", pack_id=horns.id),
        ])
        await session.commit()

    update, iq = _make_inline_update("pack:horns air")
    await bot_module.handle_inline(update, MagicMock())
    results = iq.answer.await_args.args[0]
    assert [r.audio_file_id for r in results] == ["f1"]


async def test_inline_unknown_pack_returns_empty():
    await _seed(
        Sound(name="x", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    update, iq = _make_inline_update("pack:nope")
    await bot_module.handle_inline(update, MagicMock())
    assert iq.answer.await_args.args[0] == []


async def test_post_init_calls_set_my_commands():
    ctx, bot = _ctx_with_fake_bot()
    fake_app = MagicMock()
    fake_app.bot = bot

    await bot_module._post_init(fake_app)

    cmd_calls = [c for c in bot.calls if c[0] == "set_my_commands"]
    assert len(cmd_calls) == 1
    assert set(cmd_calls[0][1]) == {"help", "random", "list"}
