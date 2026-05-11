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
from app.models import MissedSearch, Pack, PlayEvent, Sound, User
from tests.conftest import FakeBot


async def _seed(*sounds: Sound) -> None:
    async with SessionLocal() as session:
        for s in sounds:
            session.add(s)
        await session.commit()


def _make_inline_update(query: str, user_id: int = 12345):
    inline_query = MagicMock()
    inline_query.query = query
    inline_query.answer = AsyncMock()
    update = MagicMock()
    update.inline_query = inline_query
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "alice"
    update.effective_user.first_name = "Alice"
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


def _chosen_update(result_id: str, user_id: int = 555):
    chosen = MagicMock()
    chosen.result_id = result_id
    chosen.from_user = MagicMock()
    chosen.from_user.id = user_id
    update = MagicMock()
    update.chosen_inline_result = chosen
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "u"
    update.effective_user.first_name = "U"
    return update


async def test_chosen_inline_result_bumps_play_count():
    await _seed(
        Sound(name="x", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    rows = await _all_sounds()
    sid = rows[0].id

    update = _chosen_update(str(sid))
    await bot_module.handle_chosen(update, MagicMock())
    await bot_module.handle_chosen(update, MagicMock())

    rows = await _all_sounds()
    assert rows[0].play_count == 2


async def test_chosen_inline_result_ignores_non_numeric_id():
    await _seed(
        Sound(name="x", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    update = _chosen_update("not-a-number")
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


def _command_update(chat_id: int = 555, user_id: int = 67890):
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    update = MagicMock()
    update.message = msg
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "bob"
    update.effective_user.first_name = "Bob"
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
    assert set(cmd_calls[0][1]) == {"help", "random", "list", "board"}


# ---------------------------------------------------------------------------
# User tracking and PlayEvent log
# ---------------------------------------------------------------------------


async def _all_users():
    async with SessionLocal() as session:
        return (await session.execute(select(User))).scalars().all()


async def _all_events():
    async with SessionLocal() as session:
        return (await session.execute(select(PlayEvent))).scalars().all()


async def test_inline_query_upserts_user():
    update, _ = _make_inline_update("hello", user_id=42)
    await bot_module.handle_inline(update, MagicMock())

    users = await _all_users()
    assert len(users) == 1
    assert users[0].telegram_id == 42
    assert users[0].username == "alice"


async def test_touch_user_updates_existing_row():
    update1, _ = _make_inline_update("a", user_id=42)
    await bot_module.handle_inline(update1, MagicMock())
    # Same user, second hit -> still one row
    update2, _ = _make_inline_update("b", user_id=42)
    await bot_module.handle_inline(update2, MagicMock())

    users = await _all_users()
    assert len(users) == 1


async def test_chosen_inline_result_writes_play_event():
    await _seed(
        Sound(name="x", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    rows = await _all_sounds()
    sid = rows[0].id

    update = _chosen_update(str(sid), user_id=777)
    await bot_module.handle_chosen(update, MagicMock())

    events = await _all_events()
    assert len(events) == 1
    assert events[0].sound_id == sid
    assert events[0].user_id == 777


async def test_cmd_random_tracks_user():
    await _seed(
        Sound(name="only", tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    ctx, _ = _ctx_with_fake_bot()
    update, _ = _command_update(user_id=909)
    await bot_module.cmd_random(update, ctx)

    users = await _all_users()
    assert any(u.telegram_id == 909 for u in users)


# ---------------------------------------------------------------------------
# No-results telemetry
# ---------------------------------------------------------------------------


async def _all_missed():
    async with SessionLocal() as session:
        return (await session.execute(select(MissedSearch))).scalars().all()


async def test_inline_zero_result_logs_missed_search():
    update, _ = _make_inline_update("vuvuzela", user_id=42)
    await bot_module.handle_inline(update, MagicMock())

    missed = await _all_missed()
    assert len(missed) == 1
    assert missed[0].query == "vuvuzela"
    assert missed[0].user_id == 42


async def test_inline_with_results_does_not_log():
    await _seed(
        Sound(name="bruh", tags="", file_id="f1", file_unique_id="u1", kind="voice"),
    )
    update, _ = _make_inline_update("bruh", user_id=42)
    await bot_module.handle_inline(update, MagicMock())

    assert await _all_missed() == []


async def test_inline_empty_query_does_not_log():
    update, _ = _make_inline_update("", user_id=42)
    await bot_module.handle_inline(update, MagicMock())
    assert await _all_missed() == []


async def test_inline_very_short_query_does_not_log():
    update, _ = _make_inline_update("a", user_id=42)
    await bot_module.handle_inline(update, MagicMock())
    assert await _all_missed() == []


async def test_inline_unknown_pack_does_not_log():
    update, _ = _make_inline_update("pack:nope", user_id=42)
    await bot_module.handle_inline(update, MagicMock())
    assert await _all_missed() == []


# ---------------------------------------------------------------------------
# /board command and play callback
# ---------------------------------------------------------------------------


def _board_context(args=None):
    ctx = MagicMock()
    ctx.bot = FakeBot()
    ctx.args = args or []
    return ctx


async def test_cmd_board_replies_with_no_sounds_when_empty():
    update, msg = _command_update()
    await bot_module.cmd_board(update, _board_context())
    msg.reply_text.assert_awaited_once()
    assert "No sounds" in msg.reply_text.await_args.args[0]


async def test_cmd_board_posts_keyboard_with_top_sounds():
    await _seed(
        Sound(name="alpha", tags="", file_id="f1", file_unique_id="u1", kind="audio", play_count=5),
        Sound(name="beta", tags="", file_id="f2", file_unique_id="u2", kind="audio", play_count=10),
        Sound(name="gamma", tags="", file_id="f3", file_unique_id="u3", kind="audio", play_count=1),
    )
    update, msg = _command_update()
    await bot_module.cmd_board(update, _board_context())

    msg.reply_text.assert_awaited_once()
    kwargs = msg.reply_text.await_args.kwargs
    markup = kwargs["reply_markup"]
    flat = [btn for row in markup.inline_keyboard for btn in row]
    # Order: highest play_count first.
    assert [b.text for b in flat] == ["beta", "alpha", "gamma"]
    assert all(b.callback_data.startswith("play:") for b in flat)


async def test_cmd_board_pack_filter():
    async with SessionLocal() as session:
        horns = Pack(name="Horns", slug="horns")
        session.add(horns)
        await session.commit()
        session.add_all([
            Sound(name="in", tags="", file_id="f1", file_unique_id="u1",
                  kind="audio", pack_id=horns.id),
            Sound(name="out", tags="", file_id="f2", file_unique_id="u2",
                  kind="audio"),
        ])
        await session.commit()

    update, msg = _command_update()
    await bot_module.cmd_board(update, _board_context(args=["horns"]))

    markup = msg.reply_text.await_args.kwargs["reply_markup"]
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert labels == ["in"]


async def test_cmd_board_unknown_pack():
    update, msg = _command_update()
    await bot_module.cmd_board(update, _board_context(args=["does-not-exist"]))
    assert "Unknown pack" in msg.reply_text.await_args.args[0]


async def test_cmd_board_truncates_long_label():
    long_name = "x" * 80
    await _seed(
        Sound(name=long_name, tags="", file_id="f1", file_unique_id="u1", kind="audio"),
    )
    update, msg = _command_update()
    await bot_module.cmd_board(update, _board_context())
    markup = msg.reply_text.await_args.kwargs["reply_markup"]
    label = markup.inline_keyboard[0][0].text
    assert len(label) <= 28
    assert label.endswith("…")


def _callback_update(callback_data: str, user_id: int = 909, chat_id: int = 5000):
    cb = MagicMock()
    cb.data = callback_data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.message = MagicMock()
    cb.message.chat = MagicMock()
    cb.message.chat.id = chat_id
    cb.answer = AsyncMock()

    update = MagicMock()
    update.callback_query = cb
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "u"
    update.effective_user.first_name = "U"
    return update, cb


async def test_play_callback_sends_sound_and_logs():
    await _seed(
        Sound(name="snd", tags="", file_id="audfid", file_unique_id="u1", kind="audio"),
    )
    sid = (await _all_sounds())[0].id

    ctx, bot = _ctx_with_fake_bot()
    update, cb = _callback_update(f"play:{sid}", chat_id=7777)

    await bot_module.handle_play_callback(update, ctx)

    audio_sends = [c for c in bot.calls if c[0] == "audio"]
    assert len(audio_sends) == 1
    assert audio_sends[0][1] == 7777
    cb.answer.assert_awaited()

    sounds_after = await _all_sounds()
    assert sounds_after[0].play_count == 1
    async with SessionLocal() as session:
        events = (await session.execute(select(PlayEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].sound_id == sid
    assert events[0].user_id == 909


async def test_play_callback_handles_unknown_sound():
    ctx, bot = _ctx_with_fake_bot()
    update, cb = _callback_update("play:9999")

    await bot_module.handle_play_callback(update, ctx)

    cb.answer.assert_awaited()
    # No send_* call.
    assert bot.calls == []


async def test_play_callback_ignores_bad_data():
    ctx, bot = _ctx_with_fake_bot()
    update, cb = _callback_update("play:not-a-number")
    await bot_module.handle_play_callback(update, ctx)
    cb.answer.assert_awaited()
    assert bot.calls == []
