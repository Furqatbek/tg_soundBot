"""Telegram Login Widget signature verification.

See https://core.telegram.org/widgets/login#checking-authorization
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping


def verify_telegram_auth(
    params: Mapping[str, str], bot_token: str, *, max_age_seconds: int = 86400
) -> bool:
    """Validate the auth payload Telegram redirected with.

    The payload is the URL query Telegram sends to ``data-auth-url``:
    ``id``, ``first_name``, ``username``, ``photo_url``, ``auth_date``,
    ``hash`` (and possibly ``last_name``). We rebuild the data-check
    string per Telegram's spec and HMAC-SHA256 it with the SHA256 of
    the bot token.
    """
    received_hash = params.get("hash")
    if not received_hash:
        return False

    auth_date = params.get("auth_date")
    if auth_date is not None:
        try:
            if int(auth_date) < int(time.time()) - max_age_seconds:
                return False
        except ValueError:
            return False

    pairs = sorted(
        f"{k}={v}" for k, v in params.items() if k != "hash"
    )
    data_check_string = "\n".join(pairs)

    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received_hash)
