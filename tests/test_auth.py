"""Unit tests for the Telegram Login Widget signature verifier."""

from __future__ import annotations

import hashlib
import hmac
import time

from app.auth import verify_telegram_auth


def _sign(payload: dict, token: str) -> str:
    pairs = sorted(f"{k}={v}" for k, v in payload.items() if k != "hash")
    string = "\n".join(pairs)
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, string.encode(), hashlib.sha256).hexdigest()


TOKEN = "123:abc"


def test_valid_payload_passes():
    payload = {
        "id": "111",
        "first_name": "Alice",
        "username": "alice",
        "auth_date": str(int(time.time())),
    }
    payload["hash"] = _sign(payload, TOKEN)
    assert verify_telegram_auth(payload, TOKEN)


def test_tampered_field_fails():
    payload = {
        "id": "111",
        "first_name": "Alice",
        "auth_date": str(int(time.time())),
    }
    payload["hash"] = _sign(payload, TOKEN)
    payload["id"] = "222"  # tamper after signing
    assert not verify_telegram_auth(payload, TOKEN)


def test_wrong_token_fails():
    payload = {"id": "111", "auth_date": str(int(time.time()))}
    payload["hash"] = _sign(payload, TOKEN)
    assert not verify_telegram_auth(payload, "different:token")


def test_missing_hash_fails():
    payload = {"id": "111", "auth_date": str(int(time.time()))}
    assert not verify_telegram_auth(payload, TOKEN)


def test_stale_auth_date_fails():
    payload = {
        "id": "111",
        "auth_date": str(int(time.time()) - 10_000),
    }
    payload["hash"] = _sign(payload, TOKEN)
    assert not verify_telegram_auth(payload, TOKEN, max_age_seconds=300)


def test_fresh_auth_date_within_window():
    payload = {
        "id": "111",
        "auth_date": str(int(time.time()) - 60),
    }
    payload["hash"] = _sign(payload, TOKEN)
    assert verify_telegram_auth(payload, TOKEN, max_age_seconds=300)
