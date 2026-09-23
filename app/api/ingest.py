"""Загрузка записи, транскрибированной на чужой машине (`tools/ingest.py` → сюда).

Четыре ручки и один принцип: сервер принимает только то, что перечислено, и делает с этим только
то, что записано в `app/content/ingest.py`. Манифест → стейджинг; файлы — потоком на диск по
белому списку имён; `finish` — проверка комплекта и задача в очередь (та же, что у пересборок:
приём и индексация идут строго по одной); статус — из стейджинга, переживает рестарт.

Рубежи — как у правки: флаг `ingest.enabled` (в примере конфига выключен), отказ сервера, и
КТО грузит — право `ingest` (любой вошедший) при включённом входе, иначе только петля.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from functools import partial
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from ..auth import session
from ..config import APP_DIR, family_dir
from ..content import dist, ingest as core
from .voices import require

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
log = logging.getLogger(__name__)

GB = 1024 ** 3
# Установщик лежит в инструментах рядом с сайтом (тот же репозиторий): сервер лишь подставляет в
# него свой адрес и пропуск. Имя внутри zip видит человек в Загрузках — поэтому по-русски.
INSTALLER = APP_DIR.parent / "tools" / "install-mac.sh"
APP_COMMAND = "Установить «Загрузить запись».command"


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

    llm_env = core.llm_env_of(app.state)

    async def job() -> None:
        await core.accept(staging, rid, family=family, cfg=cfg, root=root, llm_env=llm_env)
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


# --- раздача установщика ----------------------------------------------------------------
# Вошедший берёт на странице пропуск и строку установки; `curl` и установщик ходят по пропуску
# (`/get/<пропуск>/…`, мимо cookie — см. `app/content/dist.py` и `PUBLIC_PREFIX` гейта).


def _mirror(request: Request) -> Path:
    """Каталог зеркала — или 404. Проверка `ingest.enabled` та же, что у приёма записей."""
    cfg = request.app.state.cfg.ingest
    if not (cfg.enabled and cfg.dist_dir):
        raise HTTPException(404, "раздача установщика не настроена (ingest.dist_dir)")
    root = Path(cfg.dist_dir)
    if not root.is_dir():
        raise HTTPException(404, "каталог раздачи не найден на сервере")
    return root


def _pass(request: Request) -> str:
    return dist.issue(request.app.state.auth.derived_secret(dist.PURPOSE))


def _site(request: Request) -> str:
    """Адрес сайта так, как его видит браузер человека: по нему установщик потом качает зеркало.

    ⚠️ Схему берём у доверенного прокси (`X-Forwarded-Proto`), а не у соединения: сайт стоит за
    apache, и без этого в команду установки уехал бы `http://` — а по нему сервер отвечает
    редиректом, и `curl … | sh` молча получил бы пустой скрипт."""
    hops = request.app.state.cfg.server.trusted_proxy_hops
    scheme = "https" if session.is_https(request.headers, request.url.scheme, hops) else "http"
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


@router.get("/dist")
async def mirror(request: Request) -> dict:
    """Что раздаём и чем это ставить. Под сессией и правом `ingest` — как сама загрузка."""
    _staging(request)
    root = _mirror(request)
    token = _pass(request)
    site = _site(request)
    data = dist.catalog(root)
    return {"files": [{k: v for k, v in f.items() if k != "sha256"} for f in data.get("files") or []],
            "bytes": data.get("bytes", 0), "built": data.get("built", ""),
            "install": f"curl -fsSL {site}/api/ingest/get/{token}/install | sh",
            "app": "/api/ingest/app.zip", "days": dist.TTL // 86400}


@router.get("/app.zip")
async def app_zip(request: Request):
    """Приложение «одним файлом»: zip с `.command`, который запускает ту же установку.

    Внутри не программа, а девять строк: двойной щелчок по скачанному файлу открывает Терминал и
    ставит всё с зеркала. Так и задумано — раздавать готовый `.app` значило бы подписывать его у
    Apple и нотаризовать, а собранное НА МЕСТЕ приложение Gatekeeper не проверяет вовсе.
    """
    _staging(request)
    _mirror(request)
    site, token = _site(request), _pass(request)
    command = ("#!/bin/sh\n"
               "# Установка «Загрузить запись» — всё скачается с сайта, пароль администратора не нужен.\n"
               "clear\n"
               f'echo "Ставлю «Загрузить запись» с {site}"\n'
               "echo\n"
               f'curl -fsSL "{site}/api/ingest/get/{token}/install" | sh\n'
               'echo\n'
               'echo "Окно можно закрыть."\n')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo(APP_COMMAND, date_time=time.localtime()[:6])
        info.external_attr = 0o755 << 16        # без бита запуска Finder откроет файл текстом
        info.create_system = 3                  # unix: иначе права из `external_attr` не читаются
        zf.writestr(info, command)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="morag-ingest-mac.zip"',
                             "Cache-Control": "no-store"})


@router.get("/get/{token}/install")
async def install(request: Request, token: str):
    """Установщик с подставленными адресом и пропуском — тело `curl … | sh`."""
    root = _mirror(request)
    if not dist.valid(token, request.app.state.auth.derived_secret(dist.PURPOSE)):
        raise HTTPException(403, "ссылка на установку просрочена — откройте страницу загрузки заново")
    script = INSTALLER.read_text(encoding="utf-8") if INSTALLER.is_file() else ""
    if not script:
        raise HTTPException(500, "установщик не найден рядом с сайтом (tools/install-mac.sh)")
    text = (script.replace("@SITE@", _site(request)).replace("@TOKEN@", token)
                  .replace("@BUILT@", str(dist.catalog(root).get("built") or "")))
    return PlainTextResponse(text, media_type="text/x-shellscript; charset=utf-8",
                             headers={"Cache-Control": "no-store"})


@router.get("/get/{token}/file/{name}")
async def mirror_file(request: Request, token: str, name: str):
    """Файл зеркала. `Range` даёт докачку — полтора гигабайта по корпоративной сети рвутся."""
    root = _mirror(request)
    if not dist.valid(token, request.app.state.auth.derived_secret(dist.PURPOSE)):
        raise HTTPException(403, "пропуск просрочен — откройте страницу загрузки заново")
    path = dist.file_of(root, name)
    if path is None:
        raise HTTPException(404, f"нет такого файла на зеркале: {name}")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.get("/{rid}")
async def status(request: Request, rid: str) -> dict:
    staging = _staging(request)
    try:
        out = staging.status(rid)
    except core.Refused as error:
        raise _refused(error)
    out["queue"] = request.app.state.rebuilder.status()
    return out
