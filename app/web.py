from __future__ import annotations

import io
import mimetypes
import os
from contextlib import asynccontextmanager
from typing import List

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
from .models import Sound

TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _logged_in(request: Request) -> bool:
    return request.session.get("user") == ADMIN_USERNAME


def _redirect_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


async def _save_one(bot_app, file: UploadFile, name: str, tags: str) -> None:
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
        async with SessionLocal() as session:
            stmt = select(Sound).order_by(
                Sound.play_count.desc(), Sound.created_at.desc()
            )
            if q:
                like = f"%{q}%"
                stmt = stmt.where(
                    (Sound.name.ilike(like)) | (Sound.tags.ilike(like))
                )
            sounds = (await session.execute(stmt)).scalars().all()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"sounds": sounds, "q": q},
        )

    @app.post("/sounds")
    async def upload_sound(
        request: Request,
        name: str = Form(...),
        tags: str = Form(""),
        file: UploadFile = File(...),
    ):
        if not _logged_in(request):
            return _redirect_login()
        await _save_one(bot_app, file, name, tags)
        return RedirectResponse("/", status_code=303)

    @app.post("/sounds/bulk")
    async def upload_bulk(
        request: Request,
        tags: str = Form(""),
        files: List[UploadFile] = File(...),
    ):
        if not _logged_in(request):
            return _redirect_login()
        for f in files:
            if not f.filename:
                continue
            name = os.path.splitext(os.path.basename(f.filename))[0]
            if not name:
                continue
            await _save_one(bot_app, f, name, tags)
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
