import os

from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


BOT_TOKEN = _required("BOT_TOKEN")
ADMIN_CHAT_ID = int(_required("ADMIN_CHAT_ID"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = _required("ADMIN_PASSWORD")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./sounds.db")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./uploads")
PORT = int(os.environ.get("PORT", "8000"))
