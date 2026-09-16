"""Кадры движка → наш поток в браузер.

Устойчивость важнее строгости: один битый кадр не должен выбросить ответ,
который уже отдал тысячу токенов. Незнакомые типы событий молча считаем.
"""

from __future__ import annotations

import json
import logging
from itertools import zip_longest
from typing import Callable, Iterator

from . import frames
from .chunk_html import extract
from .sse import SSEEvent

log = logging.getLogger(__name__)

# (url, doc_id, text) → (record_id, sec, end, media); движок ничего не знает про наш корпус
Resolver = Callable[..., tuple]

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_HOLD = max(len(_THINK_OPEN), len(_THINK_CLOSE)) - 1
_LOG_CAP = 200  # кадр цитаты бывает под 12 КБ — в лог такое не пишем
_MAX_PROVENANCE = 4  # находок на цитату: первые несколько говорят всё
_MAX_QUERY = 200  # запрос агента — фраза, а не документ


def _noop_resolver(url: str, doc_id: str = "", text: str = "") -> tuple[None, None, None]:
    return None, None, None


class _ThinkStripper:
    """Вырезает <think>…</think>.

    Движок эмитит теги отдельными токенами (`yield '<think>'`), но защищаемся и от
    случая, когда тег разрезан между чанками: придерживаем короткий хвост.
    """

    def __init__(self) -> None:
        self._inside = False
        self._hold = ""

    def feed(self, text: str) -> str:
        buf = self._hold + text
        self._hold = ""
        out: list[str] = []
        while buf:
            if self._inside:
                pos = buf.find(_THINK_CLOSE)
                if pos == -1:
                    break  # всё ещё внутри рассуждений
                buf = buf[pos + len(_THINK_CLOSE) :]
                self._inside = False
                continue
            pos = buf.find(_THINK_OPEN)
            if pos == -1:
                break
            out.append(buf[:pos])
            buf = buf[pos + len(_THINK_OPEN) :]
            self._inside = True
        if not self._inside and buf:
            # хвост может быть началом тега, разрезанного между чанками
            tail = buf[-_HOLD:]
            for i in range(len(tail), 0, -1):
                if _THINK_OPEN.startswith(tail[-i:]) or _THINK_CLOSE.startswith(tail[-i:]):
                    self._hold = tail[-i:]
                    buf = buf[: len(buf) - i]
                    break
            out.append(buf)
        return "".join(out)

    def flush(self) -> str:
        tail, self._hold = self._hold, ""
        return "" if self._inside else tail


class StreamNormalizer:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolve = resolver or _noop_resolver
        self._think = _ThinkStripper()
        self._seen: set[int] = set()
        self.finished = False
        self.tokens = 0
        self.citations = 0
        self.malformed = 0
        self.unknown_events = 0
        self.answer_parts: list[str] = []
        self.citation_frames: list[dict] = []

    # --- вход -------------------------------------------------------------

    def feed(self, ev: SSEEvent) -> Iterator[dict]:
        data = ev.data.strip()
        if data == "[DONE]":
            self.finished = True
            tail = self._think.flush()
            if tail:
                yield from self._emit_token(tail)
            return
        if not data:
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            self.malformed += 1
            log.warning("нераспознанный кадр движка: %s…", data[:_LOG_CAP])
            return
        if not isinstance(obj, dict):
            return

        if "event" in obj:
            yield from self._from_event(obj["event"])
        elif "choices" in obj:
            yield from self._from_choices(obj["choices"])
        elif "error" in obj:
            self.finished = True
            log.error("движок вернул ошибку в потоке: %s", str(obj["error"])[:_LOG_CAP])
            yield frames.error("Движок вернул ошибку. Попробуйте переспросить.")

    # --- разбор -----------------------------------------------------------

    def _from_event(self, event) -> Iterator[dict]:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        data = event.get("data") or {}
        if kind == "status":
            text = str(data.get("description") or "").strip()
            if text:
                yield frames.status(text, bool(data.get("done")))
        elif kind == "citation":
            yield from self._from_citation(data)
        else:
            self.unknown_events += 1  # новые типы событий — не ошибка

    def _from_choices(self, choices) -> Iterator[dict]:
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content is None:
            content = (choice.get("message") or {}).get("content")
        if content is None:
            return  # кадр finish_reason:"stop" — не токен
        yield from self._emit_token(str(content))

    def _emit_token(self, raw: str) -> Iterator[dict]:
        text = self._think.feed(raw)
        if text:
            self.tokens += 1
            self.answer_parts.append(text)
            yield frames.token(text)

    def _from_citation(self, data) -> Iterator[dict]:
        if not isinstance(data, dict):
            return
        source = data.get("source") or {}
        metadata = data.get("metadata") or []
        documents = data.get("document") or []
        if not isinstance(metadata, list):
            metadata = []
        if not isinstance(documents, list):
            documents = []

        for meta, doc in zip_longest(metadata, documents, fillvalue=None):
            meta = meta if isinstance(meta, dict) else {}
            n = self._number(meta)
            if n in self._seen:
                continue  # в document-режиме несколько событий делят один номер
            self._seen.add(n)

            url = str(meta.get("url") or source.get("url") or "")
            label = str(source.get("name") or meta.get("name") or "")
            chunk = extract(doc if isinstance(doc, str) else "")
            text = chunk.text
            # текст нужен резолверу: по тайм-кодам внутри чанка он находит его конец
            resolved = self._resolve(url, str(meta.get("source") or ""), text)
            record_id, sec, end, media = (list(resolved) + [None, None, None, None])[:4]
            frame = frames.citation(
                n=n,
                label=label,
                # Адрес медиа: СВОЙ важнее. У корпуса без поля `url` в шапке движок не
                # оставляет цитату без адреса, а синтезирует `file://` со своим внутренним
                # путём (`file:///app/space/records/…`) — такой адрес карточка включить не
                # может, а браузеру он ещё и показывает потроха контейнера.
                url=media or (url if url.startswith(("http://", "https://")) else ""),
                text=text,
                record_id=record_id,
                sec=sec,
                end=end,
                keywords=chunk.keywords,
                found_by=self._provenance(meta.get("found_by")),
            )
            self.citations += 1
            self.citation_frames.append(frame)
            yield frame

    @staticmethod
    def _provenance(raw) -> tuple[dict, ...]:
        """Как чанк нашёлся — только знакомые поля и разумные размеры.

        Кадр движка идёт прямиком в браузер, поэтому берём allowlist, а не «всё,
        что прислали»: движок общий и однажды положит сюда что-нибудь ещё.
        """
        if not isinstance(raw, list):
            return ()
        out: list[dict] = []
        for item in raw[:_MAX_PROVENANCE]:
            if not isinstance(item, dict):
                continue
            step: dict = {}
            query = str(item.get("query") or "").strip()
            if query:
                step["query"] = query[:_MAX_QUERY]
            for key in ("tool", "step", "rank"):
                value = item.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    step[key] = value
            if step:
                out.append(step)
        return tuple(out)

    def _number(self, meta: dict) -> int:
        """`citation_number` в движке ставится условно и бывает строкой."""
        raw = meta.get("citation_number")
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return max(self._seen, default=0) + 1

    # --- итог -------------------------------------------------------------

    @property
    def answer_text(self) -> str:
        return "".join(self.answer_parts)
