# tg_soundBot

A Telegram inline bot for searching and sending sounds you uploaded
through a small web control panel.

## How it feels in Telegram

In any chat, group, or channel where the bot is allowed inline mode:

```
@your_bot meme
```

Telegram shows a list of matching sounds (searched by name and tags).
Tap one and Telegram sends it into the current chat.

## Setup

### 1. Create the bot in Telegram

1. Talk to [@BotFather](https://t.me/BotFather) and run `/newbot`.
2. Run `/setinline` and pick a placeholder like `search sounds...`.
   This is what enables the `@your_bot <query>` UI.
3. Save the bot token.

### 2. Find your admin chat id

DM [@userinfobot](https://t.me/userinfobot) to get your numeric Telegram
user id. Uploads from the web panel are forwarded into your DM with the
bot so we can capture a real Telegram `file_id`. Open a chat with your
new bot and press **Start** so it can DM you.

### 3. Configure

```bash
cp .env.example .env
# edit .env with BOT_TOKEN, ADMIN_CHAT_ID, ADMIN_PASSWORD, SESSION_SECRET
```

### 4. Run with Docker (recommended)

```bash
docker compose up --build
```

The SQLite DB is stored in a named volume (`sounds_data`), so it
survives rebuilds and `docker compose down`.

### 4b. Run without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Open http://localhost:8000, log in, and upload a sound. Then in any
Telegram chat type `@your_bot <query>` and pick a result.

## How uploads work

- `.ogg` / `.opus` files become voice messages (`InlineQueryResultCachedVoice`).
- Other audio MIME types become audio messages
  (`InlineQueryResultCachedAudio`).
- Anything else is sent as a document.

The raw bytes are also saved to `UPLOAD_DIR` (default `./uploads`,
`/data/uploads` in Docker) as `<file_unique_id><ext>`, so you keep a
local backup independent of Telegram. Deleting from the panel removes
both the DB row and the file on disk.

You can also add sounds without the web UI: just send the bot a voice,
audio, or document file from your admin account with a caption like:

```
spongebob fail | meme, fail
```

The first part is the name, the rest after `|` are comma-separated tags.

## Environment variables

| Var | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | yes | From @BotFather |
| `ADMIN_CHAT_ID` | yes | Your numeric Telegram user id |
| `ADMIN_USERNAME` | no (default `admin`) | Web panel username |
| `ADMIN_PASSWORD` | yes | Web panel password |
| `SESSION_SECRET` | recommended | Cookie signing key |
| `DATABASE_URL` | no | Defaults to `sqlite+aiosqlite:///./sounds.db` |
| `UPLOAD_DIR` | no | Where raw uploaded files are saved. Default `./uploads`, `/data/uploads` in Docker |
| `PORT` | no | Web panel port (default 8000) |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/test_bot.py` covers the inline search and admin upload handlers
with a temp SQLite DB. `tests/test_web.py` exercises the FastAPI panel
end-to-end via `TestClient`, with a fake Telegram bot recording calls.

## Layout

```
app/
  bot.py        inline + admin upload handlers
  web.py        FastAPI control panel
  main.py       runs bot polling + web server in one asyncio loop
  db.py         async SQLAlchemy engine
  models.py     Sound table
  templates/    Jinja2 templates for the panel
```
