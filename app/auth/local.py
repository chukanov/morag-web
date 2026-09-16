"""Пользователи вне каталога. Пароль хранится только хэшем `scrypt` из stdlib — без
зависимостей, чтобы код входа читался целиком (владелец читает его сам).

Формат строки: `scrypt$n$r$p$соль_b64$хэш_b64`. Параметры внутри строки, а не константой:
поднять стоимость можно, не ломая старые хэши.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P = 2 ** 14, 8, 1  # ~16 МиБ и ~50 мс на проверку: перебор дорог, вход не тормозит
MAXMEM = 64 * 1024 * 1024  # OpenSSL по умолчанию даёт 32 МиБ; при n=2**14 r=8 хватает, но явно


class BadCredentials(Exception):
    """Неверный логин или пароль. Текст снаружи ОДИН на все случаи (нет такого, неверный
    пароль, пустой, кривой логин): иначе сайт становится справочником учёток."""


def hash_password(password: str, *, n: int = N, r: int = R, p: int = P) -> str:
    if not password:
        raise ValueError("пустой пароль хэшировать незачем: вход с ним запрещён")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=32)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")
    return f"scrypt${n}${r}${p}${b64(salt)}${b64(digest)}"


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_password(password: str, stored: str) -> bool:
    """Сравнение постоянного времени; любая кривая строка в конфиге — просто «не подошло»."""
    if not password or not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        want = _unb64(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=MAXMEM, dklen=len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def find(users, login: str):
    """Локальный пользователь по логину (регистр не важен) или None."""
    login = (login or "").lower()
    for user in users or []:
        if str(user.login).lower() == login:
            return user
    return None


def authenticate(user, password: str) -> dict:
    """Проверить пароль локального пользователя. Возвращает профиль (как у каталога, но без
    групп и фото): дальше сайту всё равно, откуда человек."""
    if not verify_password(password, user.password):
        raise BadCredentials()
    return {
        "login": str(user.login).lower(),
        "provider": "local",
        "name": user.name or user.login,
        "given": "", "surname": "",
        "mail": "", "title": "", "department": "",
        "groups": [], "dn": "",
        "photo": None, "raw": None,
    }
