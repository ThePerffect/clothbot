"""Проверка initData Telegram Mini App (HMAC-SHA256 по токену бота)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class AuthError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400 * 7) -> dict:
    """Возвращает объект пользователя или бросает AuthError.

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        raise AuthError("empty init data")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received = pairs.pop("hash", None)
    if not received:
        raise AuthError("no hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("bad hash")
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as e:
        raise AuthError("bad auth_date") from e
    if max_age and time.time() - auth_date > max_age:
        raise AuthError("init data expired")
    try:
        user = json.loads(pairs.get("user", "{}"))
    except ValueError as e:
        raise AuthError("bad user") from e
    if not isinstance(user, dict) or "id" not in user:
        raise AuthError("no user")
    return user


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Подписывает initData — используется в тестах и для локальной отладки."""
    from urllib.parse import urlencode

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})
