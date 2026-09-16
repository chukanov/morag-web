"""Разбор YAML-шапки транскрипта."""

from __future__ import annotations

from pathlib import Path

import yaml

_DELIM = "---"
# шапки в корпусе укладываются в ~1 КБ; читаем с запасом, полный файл — только если не нашли конец
_HEAD_CHARS = 8192


def split_frontmatter(text: str) -> tuple[dict, str]:
    """`text` → (данные шапки, тело). Файл без шапки — не ошибка: ({}, весь текст)."""
    if not text.startswith(_DELIM):
        return {}, text
    end = text.find(f"\n{_DELIM}", len(_DELIM))
    if end == -1:
        return {}, text
    head = text[len(_DELIM) : end]
    body = text[end + len(_DELIM) + 1 :].lstrip("\n")
    data = yaml.safe_load(head) or {}
    return (data if isinstance(data, dict) else {}), body


def read_frontmatter(path: Path) -> dict:
    """Только шапка, без чтения всего файла (тела — сотни КБ)."""
    with path.open("r", encoding="utf-8") as fh:
        head = fh.read(_HEAD_CHARS)
        if head.startswith(_DELIM) and f"\n{_DELIM}" not in head[len(_DELIM) :]:
            head += fh.read()  # редкий случай очень длинной шапки
    data, _ = split_frontmatter(head)
    return data


def read_document(path: Path) -> tuple[dict, str]:
    """Шапка + тело целиком."""
    return split_frontmatter(path.read_text(encoding="utf-8"))
