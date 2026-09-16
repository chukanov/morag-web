"""Формы запросов от браузера."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str


class AskContext(BaseModel):
    """Откуда спросили: запись и секунда.

    Окно реплик вокруг собирает сервер по своему индексу — клиент его не шлёт:
    лишний вес запроса и дыра для подмены контекста.

    `scope` говорит, ЧТО именно ограничиваем: `line` — место в записи (окно реплик вокруг
    секунды, вопрос от реплики в читалке), `record` — запись целиком (кнопки «Краткое
    содержание», «Открытые вопросы» и своё поле на странице записи). Умолчание прежнее,
    поэтому старые клиенты ведут себя байт в байт как раньше.
    """

    record_id: str
    sec: float = 0
    scope: Literal["line", "record"] = "line"


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str = ""
    history: list[Message] = Field(default_factory=list)
    context: AskContext | None = None
    corpus: str | None = None
    # Нужна ли авто-тема. Решает клиент: он знает, показана ли она уже в этой
    # сессии. Не прислали — считаем по истории (первый ход = тема нужна).
    want_topic: bool | None = None
