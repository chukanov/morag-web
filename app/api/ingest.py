"""Загрузка записи, транскрибированной на чужой машине (`tools/ingest.py` → сюда).

Четыре ручки и один принцип: сервер принимает только то, что перечислено, и делает с этим только
то, что записано в `app/content/ingest.py`. Манифест → стейджинг; файлы — потоком на диск по
белому списку имён; `finish` — проверка комплекта и задача в очередь (та же, что у пересборок:
приём и индексация идут строго по одной); статус — из стейджинга, переживает рестарт.

Рубежи — как у правки: флаг `ingest.enabled` (в примере конфига выключен), отказ сервера, и
КТО грузит — право `ingest` (любой вошедший) при включённом входе, иначе только петля.
"""

from __future__ import annotations

import logging
from functools import partial

from fastapi import APIRouter, HTTPException, Request

from ..config import family_dir
from ..content import ingest as core
from .voices import require

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
log = logging.getLogger(__name__)

GB = 1024 ** 3


def _staging(request: Request) -> core.Staging:
    cfg = request.app.state.cfg
    if not cfg.ingest.enabled:
        raise HTTPException(403, "загрузка записей выключена: включается в app/config.yml (ingest.enabled)")
    require(request, "ingest")
    return request.app.state.ingest


def _refused(error: core.Refused) -> HTTPException:
    return HTTPException(error.status, str(error))


@router.get("/options")
async def options(request: Request) -> dict:
    """Что подставлять в манифест: допустимые рубрики (из правил раскладки), потолки."""
    staging = _staging(request)
    cfg = request.app.state.cfg.ingest
    return {"events": core.events_of(staging.family), "video_ext": list(core.VIDEO_EXT),
            "max_gb": cfg.max_gb, "files": list(core.FILES)}


@router.post("")
async def create(request: Request) -> dict:
    """Манифест → стейджинг. 400 — не сходится, 409 — такая запись уже есть."""
    staging = _staging(request)
    raw = await request.json()
    if not isinstance(raw, dict):
        raise HTTPException(400, "манифест — объект")
    try:
        manifest = core.validate(raw, staging.family)
        user = request.app.state.auth.user_of(request)
        manifest.uploader = f"{user.name} ({user.login})" if user else "local"
        staging.create(manifest)
    except core.Refused as error:
        raise _refused(error)
    log.info("загрузка %s начата: «%s», %s", manifest.record_id, manifest.title, manifest.uploader)
    return {"id": manifest.record_id, "files": list(core.FILES), "video": manifest.video}


@router.put("/{rid}/files/{name}")
async def upload(request: Request, rid: str, name: str) -> dict:
    """Один файл пакета — потоком на диск. Имя — из белого списка, размер — до `max_gb`,
    свободное место — не меньше `min_free_gb` (иначе 507: видео бывает по 8 ГБ)."""
    staging = _staging(request)
    cfg = request.app.state.cfg.ingest
    if not core.accept_name(name):
        raise HTTPException(400, f"файл {name} не из пакета; принимаются: {', '.join(core.FILES)}")
    try:
        manifest = staging.manifest(rid)
    except core.Refused as error:
        raise _refused(error)
    if name.startswith("video.") and name != manifest.video:
        raise HTTPException(400, f"в манифесте видео названо {manifest.video}")
    declared = int(request.headers.get("content-length") or 0)
    if declared > cfg.max_gb * GB:
        raise HTTPException(413, f"файл больше {cfg.max_gb:g} ГБ")
    if staging.free_bytes() - declared < cfg.min_free_gb * GB:
        raise HTTPException(507, "на сервере мало места — напишите администратору")
    target = staging.dir(rid) / name
    part = target.with_suffix(target.suffix + ".part")
    written = 0
    limit = int(cfg.max_gb * GB)
    try:
        with part.open("wb") as out:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"файл больше {cfg.max_gb:g} ГБ")
                out.write(chunk)
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    staging.set_status(rid, "uploading")
    return {"id": rid, "file": name, "bytes": written}


@router.post("/{rid}/finish")
async def finish(request: Request, rid: str) -> dict:
    """Комплект на месте → приём и индексация в очередь. Повторный вызов после сбоя — штатный."""
    staging = _staging(request)
    app = request.app
    cfg = app.state.cfg.ingest
    try:
        missing = staging.complete(rid)
    except core.Refused as error:
        raise _refused(error)
    if missing:
        raise HTTPException(400, "не хватает: " + "; ".join(missing))
    status = staging.status(rid)
    if status.get("state") == "done":
        return {"id": rid, "state": "done", "record": status.get("record")}
    family = family_dir(app.state.cfg)
    root = app.state.rebuilder.root
    queue = app.state.rebuilder

    async def job() -> None:
        await core.accept(staging, rid, family=family, cfg=cfg, root=root)
        url = ""
        for slug, corpus in app.state.corpora.items():
            corpus.index.refresh_if_stale()
            if any(m.id == rid for m in corpus.index.all()):
                url = f"/{slug}/rec/{rid}"
        staging.set_status(rid, staging.status(rid)["state"], record=rid, url=url)
        if cfg.index:
            queue.submit(f"index:{rid}", partial(core.index, staging, rid, cfg=cfg, root=root, family=family))

    queued = queue.submit(f"ingest:{rid}", job)
    if queued:
        staging.set_status(rid, "queued")
    return {"id": rid, "state": "queued" if queued else status.get("state"), "queue": queue.status()}


@router.get("/{rid}")
async def status(request: Request, rid: str) -> dict:
    staging = _staging(request)
    try:
        out = staging.status(rid)
    except core.Refused as error:
        raise _refused(error)
    out["queue"] = request.app.state.rebuilder.status()
    return out
