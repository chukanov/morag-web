"""Разбор SSE-потока движка.

⚠️ Измерено на живом pipelines: `data: [DONE]` отдаётся БЕЗ завершающих `\\n\\n`
(`yield f"data: [DONE]"` в контейнере), тогда как все прочие кадры — с ними.
Парсер, диспатчащий события только по пустой строке, последний кадр не увидит,
поэтому flush недособранного события на EOF — обязателен, а не «на всякий случай».

Работаем от байтов (а не от httpx-строк), чтобы прод и тесты шли одним кодом:
тест гоняет фикстуру, разрезанную по каждому байтовому смещению.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: str


async def iter_lines(chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Байтовые чанки → строки. Кириллица, разрезанная между чанками, склеивается корректно."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    buf = ""
    async for chunk in chunks:
        buf += decoder.decode(chunk)
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            yield line.rstrip("\r")
    buf += decoder.decode(b"", final=True)
    if buf:
        yield buf.rstrip("\r")  # хвост без перевода строки — тот самый [DONE]


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[SSEEvent]:
    """Строки → события. Битые строки не роняют поток."""
    event = ""
    data: list[str] = []
    async for line in lines:
        if not line:
            if data:
                yield SSEEvent(event or "message", "\n".join(data))
            event, data = "", []
            continue
        if line.startswith(":"):  # комментарий/heartbeat
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event = value
        # id/retry и незнакомые поля игнорируем
    if data:  # EOF без пустой строки — см. шапку модуля
        yield SSEEvent(event or "message", "\n".join(data))


async def iter_events_from_bytes(chunks: AsyncIterator[bytes]) -> AsyncIterator[SSEEvent]:
    async for ev in iter_sse_events(iter_lines(chunks)):
        yield ev
