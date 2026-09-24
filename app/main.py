"""Точка входа BFF.

Запуск для разработки (отдаёт и API, и статику фронта на 127.0.0.1:8080):
    python3 -m uvicorn app.main:app --reload

На сервере компании статику по-прежнему отдаёт сам BFF (apache проксирует, `deploy/server/`);
за обратным прокси, раздающим её сам, — `server.web_root: null`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import logging as applog
from . import meta as ogmeta
from .api import ask, auth as auth_api, edits, upload as upload_api, llm as llm_api, site, voices
from .auth import AuthService, gate as auth_gate
from .chat.topic import TopicMaker
from .config import APP_DIR, PRODUCT, _inside, engine_for, family_dir, load_config, load_corpora
from .engine.client import EngineClient
from .content.edits import Edits
from .content.upload import Staging
from .content.rebuild import Rebuilder
from .content.tokens import Tokens
from .content.voices import Voices
from .journal import Journal
from .limits import ConcurrencyGate, RateLimiter

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    applog.setup()

    corpora, hub = load_corpora(cfg)
    if not corpora:
        log.warning("не подключено ни одного корпуса — проверьте app/config.yml")

    default_slug = cfg.default_corpus if cfg.default_corpus in corpora else None
    if default_slug is None and corpora:
        default_slug = next(iter(corpora))

    journal_path = Path(cfg.journal.path)
    if not journal_path.is_absolute():
        journal_path = (APP_DIR / journal_path).resolve()

    app.state.cfg = cfg
    app.state.corpora = corpora
    app.state.hub = hub

    # Семья пространств: у витрины это её каталог, у корпуса из одного пространства — он сам.
    # Там лежат общие на весь корпус словари (`names.json`) и снимок голосов.
    family = family_dir(cfg)
    app.state.voices = Voices(family)
    app.state.edits = Edits(family)
    # Словарь корпуса на все пространства разом: написание ходит между ними, и «встречается ещё
    # в N записях» обязано считать по всему корпусу, а не по одному разделу.
    app.state.tokens = Tokens([d for p in corpora.values() for d in p.index.dirs])
    app.state.rebuilder = Rebuilder(cfg.editing.rebuild, APP_DIR.parent)
    app.state.rebuilder.start()
    # Загрузка записей с чужих машин: стейджинг — `<семья>/incoming`, если конфиг не сказал иначе;
    # вне `records/`, поэтому индексатор недособранного не видит (app/content/upload.py).
    app.state.upload = Staging(Path(cfg.upload.dir) if cfg.upload.dir else family / "incoming", family,
                               base=cfg.upload.speaker_base, step=cfg.upload.speaker_step)
    app.state.default_corpus = corpora.get(default_slug) if default_slug else None
    app.state.gate = ConcurrencyGate(cfg.limits.max_concurrent_streams)
    app.state.journal = Journal(journal_path, enabled=cfg.journal.enabled)

    # Вход: сессии, роли, снимки учёток. Каталог снимков — рядом с журналом, вне git и доставки.
    auth_dir = Path(cfg.auth.data_dir)
    if not auth_dir.is_absolute():
        auth_dir = (APP_DIR / auth_dir).resolve()
    app.state.auth = AuthService(cfg.auth, auth_dir)
    if cfg.auth.enabled:
        log.info("вход включён: провайдеры %s, роль по умолчанию %s",
                 app.state.auth.providers(), cfg.auth.roles.default)

    rl = cfg.limits.rate_limit
    rl_state = Path(rl.state_path)
    if not rl_state.is_absolute():
        rl_state = (APP_DIR / rl_state).resolve()
    app.state.limiter = RateLimiter(
        enabled=rl.enabled,
        burst=rl.burst,
        refill_seconds=rl.refill_seconds,
        per_ip_concurrent=rl.per_ip_concurrent,
        daily_total=rl.daily_total,
        state_path=rl_state,
        ipv6_prefix=rl.ipv6_prefix,
    )

    # ⚠️ Клиент на КАЖДОЕ пространство. Один процесс морага обслуживает ровно один конфиг и
    # одну пару коллекций — маршрутизации по запросу в движке нет, поэтому «раздельный поиск»
    # физически означает разные адреса. Пространство без своей записи в `engines` ходит в общий.
    app.state.engines = {slug: EngineClient(engine_for(cfg, slug)) for slug in corpora}
    # Вторая дверь — режим «одна запись» (15.09): свой процесс морага на том же индексе. Ключ
    # `<слаг>:record`, чтобы `ask.py` выбирал его по scope, а закрытие и пинг видели как обычный.
    for slug in corpora:
        second = engine_for(cfg, slug).record
        if second:
            app.state.engines[f"{slug}:record"] = EngineClient(second)

    # ключ для авто-темы: свой из конфига, иначе одалживаем у корпуса (секрет не дублируем)
    topic_key = cfg.topic.api_key
    if not topic_key and cfg.topic.borrow_key_from_corpus and app.state.default_corpus:
        topic_key = app.state.default_corpus.engine_llm_key()
    app.state.topic = TopicMaker(cfg.topic, topic_key)

    log.info(
        "BFF готов: витрина=%s, пространства=%s, записей=%s, движки=%s",
        "есть" if hub else "нет (одно пространство)",
        sorted(corpora),
        {s: len(p.index) for s, p in corpora.items()},
        {key: client.cfg.base_url for key, client in app.state.engines.items()},
    )
    try:
        yield
    finally:
        await app.state.rebuilder.close()
        for client in app.state.engines.values():
            await client.aclose()
        await app.state.topic.aclose()


def _web_root() -> Path | None:
    """Корень статики из конфига (в проде за Caddy может быть отключён)."""
    cfg = load_config()
    if not cfg.server.web_root:
        return None
    root = Path(cfg.server.web_root)
    return root if root.is_absolute() else (APP_DIR / root).resolve()


def _base_url(request: Request) -> str:
    """Абсолютный адрес: ссылку кладут в мессенджер, относительная там бесполезна.

    За Caddy схема приезжает заголовком: сам сервис слушает http на loopback и
    без этого выдал бы http-ссылку на https-сайт.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def create_app() -> FastAPI:
    app = FastAPI(title=PRODUCT, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.include_router(site.router)
    app.include_router(ask.router)
    app.include_router(voices.router)
    app.include_router(edits.router)
    app.include_router(upload_api.router)
    # Шлюз LLM для расшифровки на чужом маке — под приёмом записи и его правом.
    app.include_router(llm_api.router)
    app.include_router(auth_api.router)
    # Рубеж входа — ВСЕГДА, вне зависимости от статики: при выключенной авторизации пропускает
    # всё как есть. Стоит до роутинга, статики и SPA-заглушки (Starlette собирает middleware
    # снаружи роутера), поэтому аноним не доходит ни до данных, ни до мета-тегов записи.
    app.middleware("http")(auth_gate)

    # ⚠️ Наследство домена: на нём мог жить другой сайт, и у вернувшихся посетителей остаются
    # его service worker и старые адреса. Сокет закрываем аккуратно: раздача
    # статики умеет только HTTP и падала бы на нём пятисоткой (ловили на прошлом сайте с OWUI).
    @app.websocket("/{_path:path}")
    async def _reject_stale_socket(websocket: WebSocket, _path: str) -> None:
        await websocket.close(code=1001)

    @app.exception_handler(404)
    async def _spa_fallback(request: Request, exc):
        """Неизвестный путь — отдаём приложение: маршрутизация у нас на клиенте,
        а старый OWUI уводил посетителей на /error и подобные адреса.

        Здесь же подставляются мета-теги превью: через этот обработчик проходят
        ВСЕ красивые пути (`/demo/rec/2026-03-12-kafka/1234`), а боты
        мессенджеров скачивают страницу сервером и JS не выполняют.

        Но ТОЛЬКО для адресов без расширения. Иначе браузер, попросив картинку
        или скрипт, получает страницу с кодом 200 и молча считает её за ответ:
        `/favicon.ico` так отдавал HTML, и вкладка оставалась без значка.
        """
        path = request.url.path
        looks_like_file = "." in path.rsplit("/", 1)[-1]
        if request.method == "GET" and not path.startswith("/api/") and not looks_like_file:
            index = _web_root() / "index.html"
            if index.is_file():
                # Аноним сюда доходит только за открытой страницей (`/signin`) — и мета-теги
                # записи ему не показываем: заголовок и аннотация доклада это уже данные.
                anonymous = request.app.state.auth.enabled and getattr(request.state, "user", None) is None
                tags = "" if anonymous else _meta_tags(request, path)
                if tags:
                    page = ogmeta.inject(index.read_text(encoding="utf-8"), tags)
                    return HTMLResponse(page, headers={"Cache-Control": "no-store"})
                return FileResponse(index, headers={"Cache-Control": "no-store"})
        return JSONResponse({"detail": "not found"}, status_code=404)

    def _meta_tags(request: Request, path: str) -> str:
        """Что за ссылку прислали — то и покажем в превью."""
        state = request.app.state
        parts = [p for p in path.split("/") if p]
        corpus = state.corpora.get(parts[0]) if parts else None
        rest = parts[1:] if corpus else parts
        base = _base_url(request)
        url = f"{base}{path}"

        hub = getattr(state, "hub", None)
        if corpus is None and hub is not None:
            # ⚠️ Ни главная, ни оборванная ссылка не должны показывать превью СЛУЧАЙНОГО
            # пространства: у сайта из нескольких разделов «корпус по умолчанию» — это просто
            # первый по списку, и в мессенджере он выглядел бы как весь сайт.
            return ogmeta.build_tags(
                title=hub.brand.get("title") or "Записи",
                description=hub.brand.get("tagline") or hub.brand.get("about") or "",
                url=url, image="",
            )
        if corpus is None:
            corpus = getattr(state, "default_corpus", None)
        if corpus is None:
            return ""

        brand = corpus.brand or {}
        cover = brand.get("cover")
        image = f"{base}/api/brand/{cover}?slug={corpus.slug}" if cover else ""

        if len(rest) >= 2 and rest[0] == "rec":
            record = corpus.index.by_id(rest[1])
            if record is not None:
                sec = float(rest[2]) if len(rest) > 2 and rest[2].isdigit() else None
                title, description = ogmeta.describe_record(corpus, record, sec)
                return ogmeta.build_tags(title=title, description=description, url=url, image=image)

        return ogmeta.build_tags(
            title=brand.get("title") or corpus.slug,
            description=brand.get("tagline") or brand.get("about") or "",
            url=url,
            image=image,
        )

    # Статика монтируется последней, чтобы не перехватывать /api/*.
    web_root = _web_root()

    @app.get("/favicon.svg")
    async def _favicon(request: Request):
        """Значок вкладки — из бренда корпуса (`brand.favicon` у витрины или пространства по
        умолчанию), иначе общий из статики.

        Путь `/favicon.svg` исторический и открыт без сессии (apache проксирует его поимённо,
        gate пускает): под ним может лежать и PNG — браузер смотрит на Content-Type, а не на
        расширение. Файл живёт у корпуса, а не в `web/`: значок — доменный (у нас это лицо),
        фронт же generic и однажды уедет в публичный репозиторий.
        """
        state = request.app.state
        for holder in (getattr(state, "hub", None), getattr(state, "default_corpus", None)):
            name = (getattr(holder, "brand", None) or {}).get("favicon") if holder else None
            path = _inside(holder.brand_dir, str(name)) if name else None
            if path is not None:
                return FileResponse(path, headers={"Cache-Control": "public, max-age=0, must-revalidate"})
        generic = (web_root / "favicon.svg") if web_root else None
        if generic and generic.is_file():
            return FileResponse(generic, media_type="image/svg+xml")
        return JSONResponse({"detail": "not found"}, status_code=404)

    if web_root:
        if web_root.is_dir():

            @app.middleware("http")
            async def no_store_for_static(request, call_next):
                """В деве статику не кэшируем.

                ES-модули кэшируются поштучно, и браузер легко собирает граф из
                разных версий: один файл свежий, другой — вчерашний, ссылается на
                удалённый модуль, и всё приложение молча не стартует.
                """
                response = await call_next(request)
                if not request.url.path.startswith("/api/"):
                    response.headers["Cache-Control"] = "no-store"
                return response

            app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
        else:
            log.warning("web_root не найден: %s — статика не отдаётся", web_root)
    return app


app = create_app()
