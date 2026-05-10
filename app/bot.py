from __future__ import annotations

import logging

from sqlalchemy import func, select, update as sql_update
from telegram import (
    BotCommand,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedVoice,
    Update,
)
from telegram.ext import (
    Application,
    ChosenInlineResultHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from .config import ADMIN_CHAT_ID, BOT_TOKEN
from .db import SessionLocal
from .models import Pack, Sound
from .search import parse_query, search_sounds

log = logging.getLogger(__name__)


PUBLIC_COMMANDS = [
    BotCommand("help", "How to use this bot"),
    BotCommand("random", "Send a random sound"),
    BotCommand("list", "Show top sounds"),
]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    me = await context.bot.get_me()
    if update.effective_user and update.effective_user.id == ADMIN_CHAT_ID:
        await update.message.reply_text(
            "Admin connected. Upload via the control panel, or send me a "
            "voice/audio/document with a caption like:\n\n"
            "    name | tag1, tag2\n\n"
            f"Use me inline anywhere: @{me.username} <query>"
        )
    else:
        await update.message.reply_text(
            f"Type @{me.username} <query> in any chat to search sounds.\n"
            "Or use /random, /list, /help."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    me = await context.bot.get_me()
    await update.message.reply_text(
        f"Inline search: type @{me.username} <query> in any chat.\n\n"
        "Commands:\n"
        "/random - send a random sound to this chat\n"
        "/list - show the most popular sounds\n"
        "/help - this message"
    )


async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with SessionLocal() as session:
        snd = (
            await session.execute(select(Sound).order_by(func.random()).limit(1))
        ).scalar_one_or_none()
    if not snd:
        await update.message.reply_text("No sounds yet.")
        return
    chat_id = update.effective_chat.id
    if snd.kind == "voice":
        await context.bot.send_voice(chat_id, voice=snd.file_id, caption=snd.name)
    elif snd.kind == "audio":
        await context.bot.send_audio(chat_id, audio=snd.file_id, caption=snd.name)
    else:
        await context.bot.send_document(chat_id, document=snd.file_id, caption=snd.name)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Sound)
                .order_by(Sound.play_count.desc(), Sound.created_at.desc())
                .limit(15)
            )
        ).scalars().all()
    if not rows:
        await update.message.reply_text("No sounds yet.")
        return
    me = await context.bot.get_me()
    lines = [f"{i + 1}. {s.name} - {s.play_count} plays" for i, s in enumerate(rows)]
    await update.message.reply_text(
        "Top sounds:\n" + "\n".join(lines) + f"\n\nUse @{me.username} <query> inline."
    )


# ---------------------------------------------------------------------------
# Inline query
# ---------------------------------------------------------------------------


def _build_result(snd: Sound):
    rid = str(snd.id)
    if snd.kind == "voice":
        title = snd.name
        if snd.duration:
            title = f"{snd.name} ({snd.duration}s)"
        return InlineQueryResultCachedVoice(
            id=rid, voice_file_id=snd.file_id, title=title
        )
    if snd.kind == "audio":
        return InlineQueryResultCachedAudio(id=rid, audio_file_id=snd.file_id)
    description = snd.tags or None
    return InlineQueryResultCachedDocument(
        id=rid,
        title=snd.name,
        document_file_id=snd.file_id,
        description=description,
    )


async def handle_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.inline_query.query or ""
    pack_slug, text_query = parse_query(raw)

    async with SessionLocal() as session:
        pack_id = None
        if pack_slug:
            pack = (
                await session.execute(select(Pack).where(Pack.slug == pack_slug))
            ).scalar_one_or_none()
            if pack is None:
                # Unknown pack -> show nothing rather than the full catalog.
                await update.inline_query.answer([], cache_time=1, is_personal=False)
                return
            pack_id = pack.id

        rows = await search_sounds(session, text_query=text_query, pack_id=pack_id)

    results = [_build_result(s) for s in rows]
    await update.inline_query.answer(results, cache_time=1, is_personal=False)


async def handle_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bump play_count when a user picks one of our inline results."""
    chosen = update.chosen_inline_result
    if not chosen:
        return
    try:
        sid = int(chosen.result_id)
    except (TypeError, ValueError):
        return
    async with SessionLocal() as session:
        await session.execute(
            sql_update(Sound)
            .where(Sound.id == sid)
            .values(play_count=Sound.play_count + 1)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Admin upload via DM
# ---------------------------------------------------------------------------


async def handle_admin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ADMIN_CHAT_ID:
        return
    msg = update.message
    if not msg:
        return

    caption = (msg.caption or "").strip()
    if caption.startswith("[uploaded]"):
        return
    if not caption:
        await msg.reply_text(
            "Please add a caption like:  `name | tag1, tag2`",
            parse_mode="Markdown",
        )
        return

    name, sep, tags = caption.partition("|")
    name = name.strip()
    tags = tags.strip() if sep else ""
    if not name:
        await msg.reply_text("Name cannot be empty.")
        return

    if msg.voice:
        kind, obj = "voice", msg.voice
    elif msg.audio:
        kind, obj = "audio", msg.audio
    elif msg.document:
        kind, obj = "document", msg.document
    else:
        return

    async with SessionLocal() as session:
        session.add(
            Sound(
                name=name,
                tags=tags,
                file_id=obj.file_id,
                file_unique_id=obj.file_unique_id,
                kind=kind,
                mime_type=getattr(obj, "mime_type", None),
                duration=getattr(obj, "duration", None),
            )
        )
        await session.commit()
    await msg.reply_text(f"Saved: {name}")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(PUBLIC_COMMANDS)
    except Exception as exc:  # don't crash startup on a transient API error
        log.warning("set_my_commands failed: %s", exc)


def build_application() -> Application:
    app = (
        Application.builder().token(BOT_TOKEN).post_init(_post_init).build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("random", cmd_random))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(InlineQueryHandler(handle_inline))
    app.add_handler(ChosenInlineResultHandler(handle_chosen))
    app.add_handler(
        MessageHandler(
            (filters.VOICE | filters.AUDIO | filters.Document.ALL)
            & filters.ChatType.PRIVATE,
            handle_admin_upload,
        )
    )
    return app
