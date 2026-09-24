"""Шлюз LLM для расшифровки на чужом маке: сайт ходит в него своим ключом, коллега — своей сессией.

Зачем это есть. Стадии с LLM (глоссарий, финал-раунд, описание кадров) идут НА МАКЕ того, кто
выкладывает запись, — модели whisper и диаризации локальные, а вот эти стадии ходят в
корпоративный шлюз. Значит коллеге нужен был ключ: завести в OWUI, скопировать, вписать в
настройки приложения. Владелец 23.09: «вводить ключ руками — лишняя забота, можно ли без него?»

Можно, и рубеж от этого становится СТРОЖЕ, а не слабее. Человек и так вошёл на сайт доменной
учёткой, чтобы загрузить запись, — пусть стадии ходят через сайт, а ключ остаётся на сервере,
где он уже лежит для доразметки. Удостоверение — САМА СЕССИЯ: стек умеет только
`Authorization: Bearer <строка>`, и этой строкой служит cookie сайта. Отсюда даром:

  * работает ровно пока человек залогинен (14 дней со скользящим продлением);
  * **удалили снимок учётки — доступ пропал везде и сразу** (тот же механизм, что «разлогинить»);
  * убрали из группы доступа — тоже, на первом же запросе (`allowed` проверяется каждый раз);
  * в приложении ключа нет вовсе, а значит нечему утечь и нечего «вшивать».

⚠️ Форму запроса приходится оставить OpenAI-совместимой: её диктует адаптер морага, и менять
чужой публичный движок ради этого дорого. Поэтому ручка живёт ПОД загрузкой записи
(`/api/upload/llm/…`, право `upload`), а не как «LLM сайта»: это часть приёма записи.

Два рубежа, которые тут не про безопасность, а про то, чтобы сайт не замолчал и чтобы расход
был виден:
  * **сколько запросов одного человека идёт к шлюзу разом** — адаптер шлёт до восьми, а BFF это
    один процесс: без потолка двое коллег с расшифровкой заткнули бы живым посетителям
    `/api/ask`. ⚠️ Это ОЧЕРЕДЬ, а не отказ: стадия, получившая 429, упала бы, а подождать ей не
    жалко — она и так идёт минутами;
  * **строка в журнал на каждый вызов** с логином: иначе о лишнем расходе мы узнаем от шлюза, а
    не от себя.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..content import upload as core
from .voices import require

router = APIRouter(prefix="/api/upload/llm", tags=["upload"])
log = logging.getLogger(__name__)

MB = 1024 * 1024
# Заголовки, которые нельзя пересылать дальше как есть: своё удостоверение мы подставляем сами,
# а длину и кодировку посчитает httpx.
DROP = {"host", "authorization", "content-length", "connection", "accept-encoding", "cookie"}


def _who(request: Request):
    """Кто пришёл: cookie сайта или та же сессия строкой в `Authorization: Bearer`.

    Найденную личность кладём в `request.state.user` — дальше работает общий гейт `require`,
    тот же, что у правки и у приёма записи (одно место, где сказано «кто может»)."""
    auth = request.app.state.auth
    if auth.enabled and auth.user_of(request) is None:
        raw = request.headers.get("authorization", "")
        token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        found = auth.session_user(token) if token else None
        if found is not None:
            request.state.session, request.state.user = found
    require(request, "upload")
    user = auth.user_of(request)
    return user.login if user else "local"


def _gateway(request: Request) -> tuple[str, str]:
    """Адрес шлюза и ключ — те же, что у доразметки записи (`upload.enrich`)."""
    cfg = request.app.state.cfg
    if not (cfg.upload.enabled and cfg.upload.llm.enabled):
        raise HTTPException(404, "шлюз LLM через сайт выключен (upload.llm.enabled)")
    env = core.llm_env_of(request.app.state)
    if not env:
        raise HTTPException(503, "на сервере не настроен LLM-шлюз (topic.base_url и ключ корпуса)")
    return env["ASR_LLM_BASE_URL"].rstrip("/"), env["OR_KEY"]


def _slot(request: Request, login: str) -> asyncio.Semaphore:
    """Семафор на человека. Живёт на приложении: людей единицы, чистить нечего."""
    slots = getattr(request.app.state, "llm_slots", None)
    if slots is None:
        slots = request.app.state.llm_slots = {}
    limit = max(1, request.app.state.cfg.upload.llm.slots)
    if login not in slots:
        slots[login] = asyncio.Semaphore(limit)
    return slots[login]


async def _forward(request: Request, path: str, method: str = "POST") -> Response:
    """Переслать запрос шлюзу и вернуть ответ как есть.

    ⓘ Тело ответа читаем целиком, а не потоком. Стадии адаптера и описание кадров ответа не
    стримят (им нужен разобранный JSON), а держать ради гипотетического SSE живой upstream,
    семафор и клиент внутри генератора — это три места, где можно не отпустить ресурс.
    Попросят `stream: true` — SSE вернётся одним куском, клиент его так же разберёт.
    """
    login = _who(request)
    base, key = _gateway(request)
    cfg = request.app.state.cfg.upload.llm
    body = await request.body() if method == "POST" else b""
    if len(body) > cfg.max_mb * MB:
        raise HTTPException(413, f"запрос больше {cfg.max_mb:g} МБ")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP}
    headers["Authorization"] = f"Bearer {key}"

    started = time.monotonic()
    sem = _slot(request, login)
    try:
        await asyncio.wait_for(sem.acquire(), timeout=cfg.queue_timeout)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(503, "шлюз занят другими расшифровками — попробуйте позже") from None
    try:
        async with httpx.AsyncClient(timeout=cfg.timeout, trust_env=False) as client:
            answer = await client.request(method, f"{base}{path}", content=body or None, headers=headers)
    except httpx.HTTPError as error:
        log.warning("шлюз не ответил (%s): %s", login, error)
        await _note(request, login, path, 502, started, len(body), 0)
        raise HTTPException(502, f"LLM-шлюз не отвечает: {type(error).__name__}") from None
    finally:
        sem.release()

    await _note(request, login, path, answer.status_code, started, len(body), len(answer.content))
    return Response(answer.content, status_code=answer.status_code,
                    media_type=answer.headers.get("content-type", "application/json"))


async def _note(request: Request, login: str, path: str, status: int,
                started: float, sent: int, got: int) -> None:
    """Строка в журнал: кто, куда, сколько. Иначе о лишнем расходе мы узнаем от шлюза."""
    await request.app.state.journal.write({
        "kind": "upload-llm", "user": login, "path": path, "status": status,
        "ms": round((time.monotonic() - started) * 1000), "in": sent, "out": got})


@router.post("/chat/completions")
async def completions(request: Request) -> Response:
    """То, ради чего всё: стадии адаптера и описание кадров Vision."""
    return await _forward(request, "/chat/completions")


@router.get("/models")
async def models(request: Request) -> Response:
    """Проверка «шлюз отвечает» — её зовёт приложение после входа."""
    return await _forward(request, "/models", method="GET")
