from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from starlette.middleware.sessions import SessionMiddleware

from .auth import verify_telegram_auth
from .config import (
    ADMIN_CHAT_ID,
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    BOT_TOKEN,
    SESSION_SECRET,
    UPLOAD_DIR,
)
from .db import SessionLocal, init_db
from .models import Pack, Sound, User
from .search import search_sounds

log = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _logged_in(request: Request) -> bool:
    return request.session.get("user") == ADMIN_USERNAME


def _redirect_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def _slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (name or "").lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:50] or "pack"


def _coerce_pack_id(value) -> Optional[int]:
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _save_one(
    bot_app,
    file: UploadFile,
    name: str,
    tags: str,
    pack_id: Optional[int] = None,
) -> None:
    """Forward a file to the admin chat and persist a Sound row + on-disk copy."""
    data = await file.read()
    filename = file.filename or "sound"
    ext = os.path.splitext(filename)[1].lower()
    mime = (
        file.content_type
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )

    bio = io.BytesIO(data)
    bio.name = filename

    bot = bot_app.bot
    name = name.strip()
    marker = f"[uploaded] {name}"
    if mime.startswith("audio/ogg") or ext in (".ogg", ".oga", ".opus"):
        sent = await bot.send_voice(ADMIN_CHAT_ID, voice=bio, caption=marker)
        kind, obj = "voice", sent.voice
    elif mime.startswith("audio/"):
        sent = await bot.send_audio(
            ADMIN_CHAT_ID, audio=bio, caption=marker, title=name
        )
        kind, obj = "audio", sent.audio
    else:
        sent = await bot.send_document(ADMIN_CHAT_ID, document=bio, caption=marker)
        kind, obj = "document", sent.document

    storage_path = os.path.join(UPLOAD_DIR, f"{obj.file_unique_id}{ext}")
    with open(storage_path, "wb") as fh:
        fh.write(data)

    async with SessionLocal() as session:
        session.add(
            Sound(
                name=name,
                tags=tags.strip(),
                file_id=obj.file_id,
                file_unique_id=obj.file_unique_id,
                kind=kind,
                mime_type=getattr(obj, "mime_type", mime),
                duration=getattr(obj, "duration", None),
                storage_path=storage_path,
                pack_id=pack_id,
            )
        )
        await session.commit()


def create_app(bot_app) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        await init_db()
        # Cache the bot's @username for the Telegram Login Widget.
        try:
            me = await bot_app.bot.get_me()
            app_.state.bot_username = me.username
        except Exception:
            app_.state.bot_username = None
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

    def _login_context(request: Request, error: str | None = None) -> dict:
        return {
            "error": error,
            "bot_username": getattr(request.app.state, "bot_username", None),
            "password_login_enabled": ADMIN_PASSWORD is not None,
        }

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "login.html", _login_context(request)
        )

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        if (
            ADMIN_PASSWORD is not None
            and username == ADMIN_USERNAME
            and password == ADMIN_PASSWORD
        ):
            request.session["user"] = ADMIN_USERNAME
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            _login_context(request, error="Invalid credentials"),
            status_code=401,
        )

    @app.get("/auth/telegram")
    async def auth_telegram(request: Request):
        params = dict(request.query_params)
        if not verify_telegram_auth(params, BOT_TOKEN):
            raise HTTPException(status_code=401, detail="invalid telegram signature")
        try:
            tid = int(params.get("id", "0"))
        except ValueError:
            raise HTTPException(status_code=400, detail="bad id")
        if tid != ADMIN_CHAT_ID:
            raise HTTPException(status_code=403, detail="not admin")
        request.session["user"] = ADMIN_USERNAME
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    async def healthz():
        db_ok = True
        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        bot_ok = True
        try:
            await bot_app.bot.get_me()
        except Exception:
            bot_ok = False
        ok = db_ok and bot_ok
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "db": db_ok, "bot": bot_ok},
            status_code=200 if ok else 503,
        )

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return _redirect_login()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        if not _logged_in(request):
            return _redirect_login()
        q = (request.query_params.get("q") or "").strip()
        pack_slug = (request.query_params.get("pack") or "").strip().lower()

        async with SessionLocal() as session:
            packs = (
                await session.execute(select(Pack).order_by(Pack.name.asc()))
            ).scalars().all()

            current_pack: Optional[Pack] = None
            pack_id: Optional[int] = None
            if pack_slug:
                current_pack = (
                    await session.execute(
                        select(Pack).where(Pack.slug == pack_slug)
                    )
                ).scalar_one_or_none()
                pack_id = current_pack.id if current_pack else -1  # nothing matches

            sounds = await search_sounds(
                session, text_query=q, pack_id=pack_id, limit=500
            )

        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "sounds": sounds,
                "q": q,
                "packs": packs,
                "current_pack": current_pack,
            },
        )

    @app.post("/sounds")
    async def upload_sound(
        request: Request,
        name: str = Form(...),
        tags: str = Form(""),
        pack_id: str = Form(""),
        file: UploadFile = File(...),
    ):
        if not _logged_in(request):
            return _redirect_login()
        await _save_one(bot_app, file, name, tags, _coerce_pack_id(pack_id))
        return RedirectResponse("/", status_code=303)

    @app.post("/sounds/bulk")
    async def upload_bulk(
        request: Request,
        tags: str = Form(""),
        pack_id: str = Form(""),
        files: List[UploadFile] = File(...),
    ):
        if not _logged_in(request):
            return _redirect_login()
        pid = _coerce_pack_id(pack_id)
        for f in files:
            if not f.filename:
                continue
            name = os.path.splitext(os.path.basename(f.filename))[0]
            if not name:
                continue
            await _save_one(bot_app, f, name, tags, pid)
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------
    # Packs
    # ------------------------------------------------------------------

    @app.post("/packs")
    async def create_pack(request: Request, name: str = Form(...)):
        if not _logged_in(request):
            return _redirect_login()
        clean = name.strip()
        if not clean:
            return RedirectResponse("/", status_code=303)
        slug = _slugify(clean)
        async with SessionLocal() as session:
            existing = (
                await session.execute(select(Pack).where(Pack.slug == slug))
            ).scalar_one_or_none()
            if existing is None:
                session.add(Pack(name=clean, slug=slug))
                await session.commit()
        return RedirectResponse("/", status_code=303)

    @app.post("/packs/{pid}/delete")
    async def delete_pack(request: Request, pid: int):
        if not _logged_in(request):
            return _redirect_login()
        async with SessionLocal() as session:
            pack = (
                await session.execute(select(Pack).where(Pack.id == pid))
            ).scalar_one_or_none()
            if pack:
                # Detach any sounds from this pack first.
                rows = (
                    await session.execute(
                        select(Sound).where(Sound.pack_id == pid)
                    )
                ).scalars().all()
                for s in rows:
                    s.pack_id = None
                await session.delete(pack)
                await session.commit()
        return RedirectResponse("/", status_code=303)

    @app.get("/files/{sid}")
    async def get_file(request: Request, sid: int):
        if not _logged_in(request):
            raise HTTPException(status_code=401)
        async with SessionLocal() as session:
            snd = (
                await session.execute(select(Sound).where(Sound.id == sid))
            ).scalar_one_or_none()
        if not snd or not snd.storage_path or not os.path.exists(snd.storage_path):
            raise HTTPException(status_code=404)
        return FileResponse(
            snd.storage_path,
            media_type=snd.mime_type or "application/octet-stream",
        )

    @app.post("/sounds/{sid}/edit")
    async def edit_sound(
        request: Request,
        sid: int,
        name: str = Form(...),
        tags: str = Form(""),
        pack_id: str = Form(""),
    ):
        if not _logged_in(request):
            return _redirect_login()
        async with SessionLocal() as session:
            snd = (
                await session.execute(select(Sound).where(Sound.id == sid))
            ).scalar_one_or_none()
            if snd:
                snd.name = name.strip()
                snd.tags = tags.strip()
                snd.pack_id = _coerce_pack_id(pack_id)
                await session.commit()
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------
    # Stats dashboard
    # ------------------------------------------------------------------

    @app.get("/stats", response_class=HTMLResponse)
    async def stats_page(request: Request):
        if not _logged_in(request):
            return _redirect_login()

        async with SessionLocal() as session:
            top_sounds = (
                await session.execute(
                    select(Sound)
                    .order_by(Sound.play_count.desc())
                    .limit(10)
                )
            ).scalars().all()

            top_users_rows = (
                await session.execute(
                    text(
                        "SELECT u.telegram_id, u.username, u.first_name, "
                        "COUNT(*) AS plays "
                        "FROM play_events pe "
                        "JOIN users u ON pe.user_id = u.telegram_id "
                        "GROUP BY u.telegram_id "
                        "ORDER BY plays DESC LIMIT 10"
                    )
                )
            ).mappings().all()
            top_users = [dict(r) for r in top_users_rows]

            per_day_rows = (
                await session.execute(
                    text(
                        "SELECT DATE(created_at) AS day, COUNT(*) AS n "
                        "FROM play_events "
                        "WHERE created_at >= datetime('now', '-29 days') "
                        "GROUP BY DATE(created_at)"
                    )
                )
            ).mappings().all()
            day_counts = {r["day"]: r["n"] for r in per_day_rows}

            missed_rows = (
                await session.execute(
                    text(
                        "SELECT LOWER(query) AS q, COUNT(*) AS n, "
                        "MAX(created_at) AS last_seen "
                        "FROM missed_searches "
                        "WHERE created_at >= datetime('now', '-29 days') "
                        "GROUP BY LOWER(query) "
                        "ORDER BY n DESC LIMIT 15"
                    )
                )
            ).mappings().all()
            missed_searches = [dict(r) for r in missed_rows]

        today = date.today()
        timeline = []
        for i in range(30):
            d = today - timedelta(days=29 - i)
            timeline.append((d.isoformat(), int(day_counts.get(d.isoformat(), 0))))

        total_plays = sum(n for _, n in timeline)

        return TEMPLATES.TemplateResponse(
            request,
            "stats.html",
            {
                "top_sounds": top_sounds,
                "top_users": top_users,
                "timeline": timeline,
                "total_plays_30d": total_plays,
                "missed_searches": missed_searches,
            },
        )

    # ------------------------------------------------------------------
    # User dashboard + announcements
    # ------------------------------------------------------------------

    @app.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request):
        if not _logged_in(request):
            return _redirect_login()
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT u.telegram_id, u.username, u.first_name, "
                        "u.last_seen, COALESCE(p.cnt, 0) AS plays "
                        "FROM users u "
                        "LEFT JOIN ("
                        "  SELECT user_id, COUNT(*) AS cnt "
                        "  FROM play_events GROUP BY user_id"
                        ") p ON p.user_id = u.telegram_id "
                        "ORDER BY u.last_seen DESC"
                    )
                )
            ).mappings().all()
        users = [dict(r) for r in rows]
        flash = request.session.pop("flash", None)
        return TEMPLATES.TemplateResponse(
            request, "users.html", {"users": users, "flash": flash}
        )

    @app.post("/users/{tid}/message")
    async def message_user(
        request: Request, tid: int, text_body: str = Form(..., alias="text")
    ):
        if not _logged_in(request):
            return _redirect_login()
        try:
            await bot_app.bot.send_message(tid, text_body)
            request.session["flash"] = f"Sent to {tid}."
        except Exception as exc:
            log.warning("send_message to %s failed: %s", tid, exc)
            request.session["flash"] = f"Failed to send to {tid}: {exc}"
        return RedirectResponse("/users", status_code=303)

    @app.post("/users/broadcast")
    async def broadcast(request: Request, text_body: str = Form(..., alias="text")):
        if not _logged_in(request):
            return _redirect_login()
        async with SessionLocal() as session:
            users = (
                await session.execute(select(User.telegram_id))
            ).scalars().all()

        sent = 0
        failed = 0
        for tid in users:
            try:
                await bot_app.bot.send_message(tid, text_body)
                sent += 1
            except Exception as exc:
                log.warning("broadcast to %s failed: %s", tid, exc)
                failed += 1
            # Telegram's per-bot send rate is ~30 msg/s. Stay below that.
            await asyncio.sleep(0.05)

        request.session["flash"] = (
            f"Broadcast: {sent} delivered, {failed} failed (out of {len(users)})."
        )
        return RedirectResponse("/users", status_code=303)

    @app.post("/sounds/{sid}/delete")
    async def delete_sound(request: Request, sid: int):
        if not _logged_in(request):
            return _redirect_login()
        async with SessionLocal() as session:
            snd = (
                await session.execute(select(Sound).where(Sound.id == sid))
            ).scalar_one_or_none()
            if snd:
                if snd.storage_path:
                    try:
                        os.remove(snd.storage_path)
                    except FileNotFoundError:
                        pass
                await session.delete(snd)
                await session.commit()
        return RedirectResponse("/", status_code=303)

    return app
