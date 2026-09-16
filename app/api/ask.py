"""POST /api/ask — стриминг ответа движка в браузер.

Транспорт — POST + fetch-стриминг, а не EventSource: последний GET-only и, главное,
переподключается сам, а каждый переподключённый запрос — это заново запущенный
платный агентский цикл.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..chat.prompt import build_messages, compose_about_record, compose_question
from ..content.resolve import make_resolver
from ..content.transcript import load_utterances
from ..engine import frames
from ..engine.normalize import StreamNormalizer
from ..engine.sse import iter_events_from_bytes
from ..limits import Busy, RateLimited
from .schemas import AskRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["ask"])

HEARTBEAT_AFTER = 15.0  # сек тишины, после которых шлём комментарий-пульс
ENGINE_DOWN = "Движок сейчас недоступен. Попробуйте чуть позже."
ENGINE_SLOW = "Движок молчит слишком долго. Попробуйте переспросить."
NOTHING = "Движок вернул пустой ответ. Попробуйте переформулировать вопрос."


def _client_ip(request: Request, hops: int) -> str | None:
    """За Caddy берём не первый (его подделывает клиент), а отступя справа."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and hops > 0:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[max(0, len(parts) - hops)]
    return request.client.host if request.client else None


@router.post("/ask")
async def ask(request: Request, payload: AskRequest):
    state = request.app.state
    cfg = state.cfg

    corpus = state.corpora.get(payload.corpus) if payload.corpus else state.default_corpus
    if corpus is None:
        raise HTTPException(503, "корпус не настроен")
    # ⚠️ Отказ на СЕРВЕРЕ, а не спрятанная кнопка. У пространства, которое не идёт в поиск,
    # индекса нет вовсе, и вопрос к нему — не «пустой ответ», а обращение к чужой коллекции.
    if not bool((corpus.chat or {}).get("enabled", True)):
        raise HTTPException(503, "по этому разделу поиск не ведётся")

    question = payload.question.strip()
    if not question:
        raise HTTPException(400, "пустой вопрос")
    if len(question) > cfg.limits.question_max_chars:
        raise HTTPException(413, "вопрос слишком длинный")
    if question.startswith("### Task:"):
        # служебный префикс OWUI — движок молча вернёт пустой поток
        raise HTTPException(400, "недопустимый вопрос")

    # контекст чтения собираем сами: клиенту доверять окно нельзя
    composed, ctx_meta = question, None
    if payload.context:
        record = corpus.index.by_id(payload.context.record_id)
        if record and payload.context.scope == "record":
            # Вопрос про запись ЦЕЛИКОМ: реплики не читаем — в промпт они не идут, а это чтение
            # файла на каждый вопрос. Ограничение держится идентификатором записи в тексте.
            composed = compose_about_record(
                question,
                record=record,
                doc_id=corpus.doc_id_of(record),
                settings=(corpus.chat or {}).get("ask_about_record") or {},
            )
            ctx_meta = {"record_id": record.id, "scope": "record"}
        elif record:
            try:
                utterances = load_utterances(corpus.index.path_of(record))
            except OSError:
                utterances = []
            composed = compose_question(
                question,
                record=record,
                utterances=utterances,
                sec=payload.context.sec,
                settings=(corpus.chat or {}).get("ask_from_line") or {},
            )
            ctx_meta = {"record_id": record.id, "sec": payload.context.sec, "scope": "line"}

    messages = build_messages(
        composed,
        [m.model_dump() for m in payload.history],
        turns=cfg.limits.history_turns,
        history_max_chars=cfg.limits.history_max_chars,
    )

    answer_id = secrets.token_urlsafe(12)  # непредсказуемый: по нему принимаем оценку
    ip = _client_ip(request, cfg.server.trusted_proxy_hops)

    # Сначала квота посетителя, потом слот движка: 429 «слишком часто» честнее отдать
    # сразу, не занимая место в очереди на платный цикл.
    try:
        lease = await state.limiter.acquire(ip)
    except RateLimited as limited:
        log.info("лимит: отказ %s (%s)", ip, limited.message)
        raise HTTPException(
            429, limited.message, headers={"Retry-After": str(limited.retry_after)}
        ) from None

    try:
        gate = state.gate.slot()
        await gate.__aenter__()
    except Busy:
        # Вопрос не начался — квоту посетителю возвращаем.
        await state.limiter.refund(lease)
        await state.limiter.release(lease)
        raise HTTPException(503, "Сейчас отвечаю другим — попробуйте через минуту") from None

    wants_topic = payload.want_topic if payload.want_topic is not None else not payload.history

    async def stream() -> AsyncIterator[str]:
        started = time.monotonic()
        norm = StreamNormalizer(resolver=make_resolver(corpus.index, corpus.slug, corpus.media_base))
        status = "ok"
        # Тема считается по вопросу и каталогу, ответа не ждёт — поэтому уходит
        # в фон сразу и успевает появиться, пока агент ищет.
        topic_task = (
            asyncio.create_task(state.topic.for_question(question, corpus.catalog_hint()))
            if wants_topic and state.topic.enabled
            else None
        )
        log.info(
            "тема: %s (запрошена=%s, доступна=%s, история=%d)",
            "считаю" if topic_task else "пропускаю",
            wants_topic, state.topic.enabled, len(payload.history),
        )

        def topic_frame() -> str | None:
            """Готова ли тема — проверяем между кадрами, не блокируя поток."""
            nonlocal topic_task
            if topic_task is None or not topic_task.done():
                return None
            task, topic_task = topic_task, None
            try:
                topic = task.result()
            except Exception:  # тема — украшение, ответ важнее
                log.warning("тема: фоновая задача упала", exc_info=True)
                return None
            if not topic:
                log.info("тема: LLM ничего не вернул")
                return None
            log.info("тема готова: %s", topic["title"])
            return frames.encode(frames.topic(topic["title"], topic.get("summary", "")))

        # Вопрос про ОДНУ запись — во вторую дверь корпуса (`engines.<слаг>.record`), если она
        # есть: свой процесс движка, чей промпт держит границы записи сам (15.09). Нет — общий
        # движок, и ограничение держится только текстом вопроса, как раньше.
        record_key = f"{corpus.slug}:record"
        engine_key = (
            record_key if (ctx_meta or {}).get("scope") == "record" and record_key in state.engines
            else corpus.slug
        )
        try:
            yield frames.encode(frames.answer_id(answer_id))
            try:
                async with state.engines[engine_key].stream_chat(messages) as response:
                    if response.status_code != 200:
                        body = (await response.aread())[:500].decode("utf-8", "replace")
                        log.error("движок ответил %s: %s", response.status_code, body)
                        status = "error"
                        yield frames.encode(frames.error(ENGINE_DOWN))  # тело наружу не отдаём
                        return
                    last = time.monotonic()
                    async for event in iter_events_from_bytes(response.aiter_bytes()):
                        for frame in norm.feed(event):
                            yield frames.encode(frame)
                            last = time.monotonic()
                        ready = topic_frame()  # тема готова — отдаём её тут же
                        if ready:
                            yield ready
                            last = time.monotonic()
                        if norm.finished:
                            break
                        if time.monotonic() - last > HEARTBEAT_AFTER:
                            yield frames.HEARTBEAT  # чтобы прокси не закрыл соединение
                            last = time.monotonic()
            except (httpx.ConnectError, httpx.ConnectTimeout):
                status = "error"
                log.warning("движок недоступен: %s", cfg.engine.base_url)
                yield frames.encode(frames.error(ENGINE_DOWN))
                return
            except (httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                status = "error"
                log.warning("поток движка оборвался: %s", type(exc).__name__)
                yield frames.encode(frames.error(ENGINE_SLOW))
                return

            if norm.tokens == 0 and status == "ok":
                # пустой поток выглядит как зависание — говорим честно
                status = "empty"
                yield frames.encode(frames.error(NOTHING))
                return

            # Тема почти всегда успевает прийти во время поиска. Если нет —
            # ждём её совсем чуть-чуть: держать готовый ответ ради заголовка нельзя.
            if topic_task is not None:
                await asyncio.wait([topic_task], timeout=2)  # не отменяет задачу по таймауту
                ready = topic_frame()
                if ready:
                    yield ready

            yield frames.encode(frames.done())
        except asyncio.CancelledError:
            status = "aborted"
            log.info("посетитель отключился после %d токенов", norm.tokens)
            raise
        finally:
            if topic_task is not None and not topic_task.done():
                topic_task.cancel()  # посетитель ушёл — считать тему незачем
            await gate.__aexit__(None, None, None)
            # Освобождаем «один вопрос с адреса» ровно тогда же, когда слот движка:
            # держать дольше — значит наказывать за наш собственный поток.
            await state.limiter.release(lease)
            log.info(
                "ответ %s: %s, токенов=%d, цитат=%d, %.1f с",
                answer_id, status, norm.tokens, norm.citations, time.monotonic() - started,
            )
            await state.journal.write(
                {
                    "type": "answer",
                    "corpus": corpus.slug,
                    "session_id": payload.session_id,
                    "answer_id": answer_id,
                    "question": question,
                    "context": ctx_meta,
                    # Какой процесс отвечал: замер режима записи сравнивает «до/после» по журналу.
                    "engine": "record" if engine_key == record_key else "corpus",
                    "answer": norm.answer_text,
                    "citations": [
                        # ⚠️ Поле называется `rec`, а не `ep`: `ep` — подкастовый номер выпуска,
                        # и на нём журнал падал с KeyError В КАЖДОМ ответе с цитатами. Падение
                        # приходило в `finally` уже после отданного ответа, поэтому снаружи всё
                        # выглядело исправным, а в журнале не оставалось ровно тех записей,
                        # ради которых он и заведён.
                        {"n": c["n"], "label": c["label"], "url": c["url"], "rec": c["rec"], "sec": c["sec"]}
                        for c in norm.citation_frames
                    ],
                    "status": status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "ip": ip,
                    # Кто спрашивал — при включённом входе; без него None, как раньше был только ip.
                    "user": ({"login": user.login, "name": user.name}
                             if (user := getattr(request.state, "user", None)) else None),
                }
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx/Caddy не должны буферизовать поток
            "Connection": "keep-alive",
        },
    )
