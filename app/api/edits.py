"""Правка реплики с сайта и повышение правки до правила корпуса.

Рубежи те же три, что у правки имён, и берём мы их оттуда же (`voices._guard`), а не заводим
свои: выключенный флаг (умолчание), отказ СЕРВЕРА, кто правит (роль при включённой
авторизации, петлевой адрес без неё). Дублировать эту проверку значило бы однажды поправить
её в одном месте из двух. Реплика — право `edit` (любой вошедший), «починить везде» — правило
на весь корпус, право `voices` (admin).

Смотреть, где ещё встречается написание, можно всегда: это чтение корпуса, который и так открыт.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..content.edits import Refused, Stale
from .voices import _guard, _record_dirs

router = APIRouter(prefix="/api", tags=["edits"])
log = logging.getLogger(__name__)


class TurnEdit(BaseModel):
    # Номер АБЗАЦА в `record.words.json` — ровно то, что видит читалка. Перевод в координаты
    # сырца делает сервер: клиент про реплики сырца ничего не знает и знать не должен.
    turn: int
    was: str = Field(max_length=20000)
    now: str = Field(max_length=20000)


class Batch(BaseModel):
    # Правки копятся в памяти читалки и уезжают ОДНОЙ пачкой по выходу из режима: иначе запись
    # пересобиралась бы после каждого слова.
    edits: list[TurnEdit] = Field(default_factory=list, max_length=200)


class Promote(BaseModel):
    was: str = Field(max_length=120)
    now: str = Field(max_length=120)
    record: str = Field(default="", max_length=200)


def _store(request: Request):
    store = getattr(request.app.state, "edits", None)
    if store is None:
        raise HTTPException(503, "корпус не настроен")
    return store


def _dir(request: Request, record_id: str):
    dirs = _record_dirs(request, [record_id])
    if record_id not in dirs:
        raise HTTPException(404, f"записи {record_id} нет")
    return dirs[record_id]


@router.get("/tokens/{word}")
async def tokens(request: Request, word: str) -> dict:
    """Где ещё встречается написание и какие рядом варианты.

    ⚠️ Варианты — не украшение. Гарбл никогда не бывает один: замерено на живом корпусе —
    у одного термина шесть написаний, у другого двенадцать, у фамилии докладчика десять.
    """
    store = getattr(request.app.state, "tokens", None)
    if store is None:
        raise HTTPException(503, "корпус не настроен")
    return {**store.where(word), "variants": store.variants(word)}


@router.post("/records/{record_id}/edits")
async def save(request: Request, record_id: str, payload: Batch) -> dict:
    """Принять пачку правок абзацев и поставить запись в пересборку."""
    _guard(request)
    record_dir = _dir(request, record_id)
    why = request.app.state.auth.why_line(request)
    try:
        result = _store(request).save(record_dir, [e.model_dump() for e in payload.edits], why=why)
    except Stale as error:
        raise HTTPException(409, str(error))
    except Refused as error:
        raise HTTPException(400, str(error))

    queued = request.app.state.rebuilder.enqueue({record_id: record_dir}) if result["saved"] else []
    log.info("правок реплик в %s: %d", record_id, len(result["saved"]))
    return {**result, "queued": queued, "queue": request.app.state.rebuilder.status()}


@router.delete("/records/{record_id}/edits")
async def drop(request: Request, record_id: str) -> dict:
    """Снять все правки записи. Обратимость — операция, а не обещание."""
    _guard(request)
    record_dir = _dir(request, record_id)
    gone = _store(request).drop(record_id)
    queued = request.app.state.rebuilder.enqueue({record_id: record_dir}) if gone else []
    return {"record": record_id, "dropped": gone, "queued": queued,
            "queue": request.app.state.rebuilder.status()}


@router.post("/fixes")
async def promote(request: Request, payload: Promote) -> dict:
    """«Починить везде»: правка становится правилом корпуса.

    Список записей отдаём вместе с ответом — человек должен видеть масштаб того, что запустил.
    """
    _guard(request, "voices")
    store = _store(request)
    why = request.app.state.auth.why_line(request)
    try:
        result = store.promote(payload.record.strip(), payload.was, payload.now, why=why)
    except Refused as error:
        raise HTTPException(400, str(error))

    touched = [r["id"] for r in request.app.state.tokens.where(payload.was.strip())["records"]]
    if payload.record.strip() and payload.record.strip() not in touched:
        touched.append(payload.record.strip())  # там написание уже поправлено поместно
    dirs = _record_dirs(request, touched)
    queued = request.app.state.rebuilder.enqueue(dirs)
    log.info("правило «%s» → «%s», записей %d", payload.was, payload.now, len(touched))
    return {**result, "records": touched, "queued": queued,
            "queue": request.app.state.rebuilder.status()}
