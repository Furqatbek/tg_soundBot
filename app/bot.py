from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import or_, select
from telegram import (
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedVoice,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from .config import ADMIN_CHAT_ID, BOT_TOKEN
from .db import SessionLocal
from .models import Sound

log = logging.getLogger(__name__)


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
            f"Type @{me.username} <query> in any chat to search sounds."
        )


async def handle_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = (update.inline_query.query or "").strip()
    async with SessionLocal() as session:
        if query:
            like = f"%{query}%"
            stmt = (
                select(Sound)
                .where(or_(Sound.name.ilike(like), Sound.tags.ilike(like)))
                .order_by(Sound.created_at.desc())
                .limit(50)
            )
        else:
            stmt = select(Sound).order_by(Sound.created_at.desc()).limit(50)
        rows = (await session.execute(stmt)).scalars().all()

    results = []
    for snd in rows:
        rid = str(uuid4())
        if snd.kind == "voice":
            results.append(
                InlineQueryResultCachedVoice(
                    id=rid, voice_file_id=snd.file_id, title=snd.name
                )
            )
        elif snd.kind == "audio":
            results.append(
                InlineQueryResultCachedAudio(id=rid, audio_file_id=snd.file_id)
            )
        else:
            results.append(
                InlineQueryResultCachedDocument(
                    id=rid, title=snd.name, document_file_id=snd.file_id
                )
            )

    await update.inline_query.answer(results, cache_time=1, is_personal=False)


async def handle_admin_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ADMIN_CHAT_ID:
        return
    msg = update.message
    if not msg:
        return

    caption = (msg.caption or "").strip()
    # Skip files we sent ourselves from the web panel.
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


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(InlineQueryHandler(handle_inline))
    app.add_handler(
        MessageHandler(
            (filters.VOICE | filters.AUDIO | filters.Document.ALL) & filters.ChatType.PRIVATE,
            handle_admin_upload,
        )
    )
    return app
