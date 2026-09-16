"""Cookie сессии: подписанный HMAC-SHA256 токен из stdlib.

Внутри — только `{sub, prv, iat, exp}` (логин, провайдер, выдана, истекает). Ни имени, ни
роли: роль считается на каждый запрос из конфига и снимка учётки (`store.py`), поэтому смена
конфига действует сразу, а удалённый снимок делает токен бесполезным — это и есть отзыв.

Формат: `base64url(json).base64url(hmac)`. Без `itsdangerous`/JWT намеренно — тридцать строк
своих читаются быстрее чужой библиотеки, а владелец читает код входа сам.
"""

from __future__ import annotations

import base64
import hmac
import json
import hashlib
import os
import secrets
import time
from pathlib import Path

COOKIE = "morag_session"


def load_or_create_secret(path: Path) -> bytes:
    """Ключ подписи. Нет файла — создаём 32 случайных байта с правами 0600: `app/data` вне git
    и вне доставки, поэтому у каждой машины ключ свой, и cookie с ноутбука на сервере не
    годится (и наоборот)."""
    if path.is_file():
        secret = path.read_bytes().strip()
        if len(secret) >= 32:
            return secret
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = base64.urlsafe_b64encode(secrets.token_bytes(48))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(secret)
    return secret


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict, secret: bytes) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    mac = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def verify(token: str | None, secret: bytes, now: float | None = None) -> dict | None:
    """Полезная нагрузка токена или None: подпись не сошлась, срок вышел, формат кривой —
    всё одинаково «сессии нет», без различий наружу."""
    if not token or "." not in token:
        return None
    body, _, mac = token.partition(".")
    try:
        want = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(mac)):
            return None
        payload = json.loads(_unb64(body).decode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("sub") or not payload.get("prv"):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or exp <= (now if now is not None else time.time()):
        return None
    return payload


def issue(login: str, provider: str, lifetime_s: float, now: float | None = None) -> dict:
    now = int(now if now is not None else time.time())
    return {"sub": login, "prv": provider, "iat": now, "exp": now + int(lifetime_s)}


def should_renew(payload: dict, lifetime_s: float, now: float | None = None) -> bool:
    """Скользящее продление: токен старше восьмой части срока переиздаётся на полный срок.
    Не на каждый запрос — иначе каждый ответ нёс бы новую cookie, включая SSE-потоки."""
    now = now if now is not None else time.time()
    iat = payload.get("iat")
    return not isinstance(iat, (int, float)) or now - iat > lifetime_s / 8


def is_https(headers, url_scheme: str, trusted_hops: int) -> bool:
    """Схема запроса для флага `Secure`. За доверенным прокси (`trusted_proxy_hops > 0`) —
    из `X-Forwarded-Proto`; без прокси заголовку верить нельзя (его пришлёт кто угодно), и
    решает схема самого соединения. Локально это http — cookie без `Secure`, иначе браузер
    её просто не примет и вход «не работает» без единой ошибки."""
    if trusted_hops > 0:
        forwarded = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if forwarded:
            return forwarded == "https"
    return url_scheme == "https"
