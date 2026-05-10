from __future__ import annotations

import io
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from .config import (
    ADMIN_CHAT_ID,
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    SESSION_SECRET,
    UPLOAD_DIR,
)
from .db import SessionLocal, init_db
from .models import Pack, Sound
from .search import search_sounds

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
    async def lifespan(_: FastAPI):
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        await init_db()
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            request.session["user"] = username
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid credentials"},
            status_code=401,
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
