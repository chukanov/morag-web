"""Логи без утечек: ключ движка и гигантские кадры в них попасть не должны."""

from __future__ import annotations

import logging
import re

_BEARER = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)
_API_KEY = re.compile(r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", re.IGNORECASE)
# Пароли входа: в лог они попадать не должны вовсе, но если чей-то дамп запроса их принесёт —
# маскируем, как ключи.
_PASSWORD = re.compile(r"((?:bind_)?password['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", re.IGNORECASE)
_MAX = 400  # кадр цитаты бывает в килобайтах — целиком в лог не пишем


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = _PASSWORD.sub(r"\1***", _API_KEY.sub(r"\1***", _BEARER.sub(r"\1***", message)))
        if len(clean) > _MAX:
            clean = clean[:_MAX] + f"… (+{len(clean) - _MAX} симв.)"
        if clean != message:
            record.msg, record.args = clean, ()
        return True


def setup(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
