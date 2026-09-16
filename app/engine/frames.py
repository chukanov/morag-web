"""Кадры нашего потока в браузер — контракт, которым владеем мы."""

from __future__ import annotations

import json


def answer_id(value: str) -> dict:
    return {"type": "answer_id", "id": value}


def token(text: str) -> dict:
    return {"type": "token", "text": text}


def status(text: str, done: bool = False) -> dict:
    return {"type": "status", "text": text, "done": done}


def citation(
    n: int,
    label: str,
    url: str,
    text: str,
    record_id: str | None = None,
    sec: int | None = None,
    end: int | None = None,
    keywords: tuple[str, ...] = (),
    found_by: tuple[dict, ...] = (),
) -> dict:
    # rec/sec/end — наши, из своего индекса: метку движка парсить нельзя, это заголовок
    # доклада и в нём может быть что угодно, включая наш разделитель.
    # end нужен волне: без него она рисовала бы фиксированное окно, а чанки разной длины.
    # keywords — слова, которые движок сам отметил как совпавшие с запросами агента:
    # по ним карточка показывает суть, не заставляя читать полторы минуты речи.
    # found_by — как фрагмент нашёлся: запрос агента, инструмент, шаг, место в
    # выдаче. Единственный честный ответ на «почему это здесь»; всё остальное
    # приходится домысливать.
    return {
        "type": "citation",
        "n": n,
        "label": label,
        "url": url,
        "text": text,
        "rec": record_id,
        "sec": sec,
        "end": end,
        "keywords": list(keywords),
        "found_by": list(found_by),
    }


def topic(title: str, summary: str = "") -> dict:
    return {"type": "topic", "title": title, "summary": summary}


def error(message: str) -> dict:
    return {"type": "error", "message": message}


def done() -> dict:
    return {"type": "done"}


def encode(frame: dict) -> str:
    """В отличие от движка, завершаем `\\n\\n` каждый кадр, включая последний."""
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"


HEARTBEAT = ": hb\n\n"  # чтобы прокси не закрыл соединение, пока агент ищет
