"""Голоса корпуса: снимок для верстака имён и правка словаря.

Это запись в корпус со стороны сайта. Рубежей три, и они не дублируют друг друга:
выключенный флаг (умолчание), отказ СЕРВЕРА (а не спрятанная кнопка) и КТО правит — роль
вошедшего при включённой авторизации (имя голоса действует на весь корпус, поэтому `admin`),
только петлевой адрес без неё. Подробности — `app/config.py`, `EditingCfg` и `AuthCfg`.

Читать снимок можно всегда: смотреть, кто сколько говорил, — не правка.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth.roles import PERMISSIONS, allows
from ..content import registry
from .ask import _client_ip

router = APIRouter(prefix="/api", tags=["voices"])
log = logging.getLogger(__name__)

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class Rename(BaseModel):
    # Пустое имя = снять правку. Обратимость — операция, а не обещание.
    name: str = Field(default="", max_length=120)
    why: str = Field(default="", max_length=600)
    # Задан — правка адресная, только для этой записи. Нужна там, где реестр склеил двух людей.
    record: str = Field(default="", max_length=200)
    # «Это я»: назвать безымянный голос ВОШЕДШИМ. Имя берётся из сессии, а не из `name` —
    # подсунуть чужое нельзя; правка всегда на весь корпус (голос — id реестра, человек — везде).
    claim: bool = False


def _voices(request: Request):
    voices = getattr(request.app.state, "voices", None)
    if voices is None:
        raise HTTPException(503, "корпус не настроен")
    return voices


def _guard(request: Request, need: str = "edit") -> None:
    """Три рубежа. Каждый ловит свой промах, поэтому ни один не лишний.

    `need` — право из `roles.PERMISSIONS` (`edit` — реплики, `voices` — имена голосов и
    правила словаря). При выключенной авторизации оно не читается вовсе: третий рубеж тогда —
    петлевой адрес, и поведение то же, что до появления входа.
    """
    cfg = request.app.state.cfg
    if not cfg.editing.enabled:
        raise HTTPException(403, "правка выключена: включается в app/config.yml, который вне git")
    require(request, need)


def require(request: Request, need: str) -> None:
    """Третий рубеж сам по себе — КТО: право `need` при включённом входе, иначе петлевой адрес.
    Общий для правки и для загрузки записей (`api/upload.py`): у каждой свой флаг включения,
    а ответ на «кто» один, и править его в двух местах значило бы однажды поправить в одном."""
    cfg = request.app.state.cfg
    auth = request.app.state.auth
    if auth.enabled:
        user = auth.user_of(request)
        if user is None:
            raise HTTPException(401, "нужен вход")
        if not allows(user.role, PERMISSIONS[need]):
            raise HTTPException(403, f"нужна роль {PERMISSIONS[need]}, у вас — {user.role}")
        return
    if cfg.editing.local_only:
        ip = _client_ip(request, cfg.server.trusted_proxy_hops)
        if ip not in LOOPBACK:
            raise HTTPException(403, "правка принимается только с этой машины")


def _record_dirs(request: Request, ids: list[str]) -> dict[str, Path]:
    """id записи → её каталог. Ищем по индексам пространств: где лежит запись, знают они.

    ⚠️ Пространств несколько, и голос ходит между ними — 79 голосов звучат больше чем в одном.
    Искать в одном корне значило бы пересобрать часть записей и тихо оставить остальные.
    """
    want = set(ids)
    out: dict[str, Path] = {}
    for corpus in request.app.state.corpora.values():
        for meta in corpus.index.all():
            if meta.id in want and meta.id not in out:
                out[meta.id] = Path(meta.file).parent
    return out


@router.get("/voices")
async def voices(request: Request) -> dict:
    """Снимок голосов. Имена — из словаря, всё остальное — из снимка (см. Voices.public)."""
    data = _voices(request).public()
    cfg = request.app.state.cfg
    data["editing"] = request.app.state.auth.capabilities(request, cfg.editing.enabled)["voices"]
    if not data["ready"]:
        data["hint"] = "снимок не собран: python3 tools/voices.py --scan"
    return data


# --- узнавание голоса по отпечатку ------------------------------------------------------
# ⚠️ Объявлены ДО `/voices/{voice}`, как и `/voices/queue`: иначе «identify» уехал бы в параметр
# и попал бы в карточку голоса с таким именем.


def _registry_path(request: Request) -> Path:
    cfg = request.app.state.cfg.voices
    if not cfg.registry:
        raise HTTPException(404, "реестр голосов не настроен (voices.registry)")
    return Path(cfg.registry)


@router.post("/voices/identify")
async def identify(request: Request) -> dict:
    """Кто это говорит: отпечатки голосов записи → номера корпуса.

    Зовёт машина, которая расшифровала запись у себя: у неё свой счёт голосов, и подписывать по
    нему нельзя — её `Speaker_3` не наш. Право то же, что у загрузки записи (`upload`): узнавание
    и есть часть приёма, а отдельной «регистрации голоса» не существует — регистрировать без
    отпечатка нечего.

    `dry: true` — только показать карту: узнавание иначе занимает номера под запись, которую
    могли и не принять.
    """
    require(request, "upload")
    path = _registry_path(request)
    cfg = request.app.state.cfg.voices
    body = await request.json()
    voices = (body or {}).get("voices") if isinstance(body, dict) else None
    if not isinstance(voices, dict) or not voices:
        raise HTTPException(400, "нужны отпечатки: {\"voices\": {\"Speaker_0\": {\"centroid\": [...], \"air_sec\": 12.3}}}")
    dry = bool((body or {}).get("dry"))
    try:
        mapping, report = registry.identify(
            path, voices, episode=str((body or {}).get("episode") or ""),
            threshold=cfg.match_threshold, suspect=cfg.suspect_threshold,
            short_air_sec=cfg.short_air_min * 60, max_centroids=cfg.max_centroids, dry=dry)
    except registry.RegistryError as error:
        raise HTTPException(503, str(error)) from None
    log.info("узнавание %s%s: %s", (body or {}).get("episode") or "—", " (предпросмотр)" if dry else "",
             ", ".join(f"{r['from']}→{r['to']} ({r['how']})" for r in report))
    return {"map": mapping, "report": report, "dry": dry}


@router.get("/voices/registry")
async def registry_stats(request: Request) -> dict:
    """Состояние реестра: сколько голосов, следующий номер, когда менялся. Векторов не отдаёт."""
    require(request, "upload")
    try:
        return registry.stats(_registry_path(request))
    except registry.RegistryError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/voices/{voice}")
async def rename(request: Request, voice: str, payload: Rename) -> dict:
    """Записать имя голоса. Возвращает записи, которые пойдут в пересборку.

    Список отдаём ДО того, как пересборка началась: у самого частого голоса корпуса 47 записей,
    и человек должен видеть масштаб, а не узнавать о нём по факту.
    """
    store = _voices(request)
    auth = request.app.state.auth
    if payload.claim:
        # Право у любого вошедшего, но только на БЕЗЫМЯННЫЙ голос: названный меняет админ.
        _guard(request, "claim")
        user = auth.user_of(request)
        if user is None or not user.speaker_name:
            raise HTTPException(400, "«это я» работает только для вошедшего с именем")
        named, _ = store.names()
        if named.get(voice):
            raise HTTPException(409, f"голос уже назван: {named[voice]} — изменить имя может администратор")
        name, record = user.speaker_name, ""
        why = auth.claim_line(request, subject=name)
    else:
        _guard(request, "voices")
        name, record = payload.name.strip(), payload.record.strip()
        # Подпись: с именем вошедшего, а без авторизации — честное «автор неизвестен».
        why = payload.why.strip() or (
            (auth.why_line(request, subject=name)
             or f"{name} — правка владельца, {date.today().isoformat()}, локально. "
                f"⚠️ Автор неизвестен: авторизации пока нет.") if name else "")
    try:
        touched = store.rename(voice, name, why, record=record)
    except ValueError as error:
        raise HTTPException(400, str(error))

    dirs = _record_dirs(request, touched)
    missing = [rid for rid in touched if rid not in dirs]
    queued = request.app.state.rebuilder.enqueue(dirs)
    log.info("голос %s → %r, записей %d, в очередь %d", voice, name or "—", len(touched), len(queued))
    return {
        "voice": voice,
        "name": name,
        "records": touched,
        "queued": queued,
        # Запись есть в снимке, но её каталога нет: снимок устарел. Молчать об этом нельзя —
        # человек решит, что имя применилось везде.
        "missing": missing,
        "queue": request.app.state.rebuilder.status(),
    }


@router.get("/voices/queue")
async def queue(request: Request) -> dict:
    """Что осталось пересобрать. Верстак по этому показывает, когда правка доехала до файлов."""
    return request.app.state.rebuilder.status()


@router.get("/voices/{voice}")
async def one(request: Request, voice: str, record: str = "") -> dict:
    """Один голос — для правки прямо в читалке.

    ⚠️ Объявлен ПОСЛЕ `/voices/queue`: маршруты разбираются по порядку, и иначе «queue» уехал бы
    в параметр. Читать можно всегда — смотреть, кто говорит, не правка; флаг отдаём, чтобы
    читалка знала, показывать ли поле ввода.
    """
    data = _voices(request).one(voice, record.strip())
    cfg = request.app.state.cfg
    data["editing"] = request.app.state.auth.capabilities(request, cfg.editing.enabled)["voices"]
    return data
