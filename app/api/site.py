"""Бренд, тема, записи и оценки ответов."""

from __future__ import annotations

import json

from hashlib import md5
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..config import engine_for
from ..content import words

router = APIRouter(prefix="/api", tags=["site"])

# Потолок «шире» у карточки-момента: дальше человеку нужна читалка, а не растущая
# карточка — там для длинного чтения есть прокрутка, слежение и плеер.
_MAX_PAD = 6


class Feedback(BaseModel):
    answer_id: str
    vote: str = Field(pattern="^(up|down)$")
    comment: str = ""


def _corpus(request: Request, slug: str | None = None):
    corpora = request.app.state.corpora
    if not corpora:
        raise HTTPException(503, "корпуса не настроены")
    if slug:
        corpus = corpora.get(slug)
        if not corpus:
            raise HTTPException(404, "нет такого корпуса")
        return corpus
    return request.app.state.default_corpus


def _meta(corpus, record_id: str):
    meta = corpus.index.by_id(record_id)
    if meta is None:
        raise HTTPException(404, "нет такой записи")
    return meta


@router.get("/site")
async def site(request: Request, slug: str | None = None) -> dict:
    # Флаг правки едет вместе с описанием пространства: без него читалка не знает, показывать ли
    # тумблер «Править». ⚠️ Это ПОДСКАЗКА интерфейсу, а не рубеж — отказ даёт сервер, и он же
    # проверяет роль (или адрес, если авторизации нет). Спрятанная кнопка защитой не является.
    state = request.app.state
    return {**_corpus(request, slug).public(),
            "editing": state.auth.capabilities(request, state.cfg.editing.enabled)["edit"]}


@router.get("/corpora")
async def corpora(request: Request) -> dict:
    """Витрина: какие пространства есть, сколько в каждом записей и часов.

    Единственный запрос, который нужен главной. Числа считаются по индексу, а не берутся из
    конфига: вписанное руками число устаревает молча и врёт ровно тогда, когда корпус растёт.

    Порядок — из `hub.yml`. Не попавшее в него пространство уходит в КОНЕЦ, но остаётся видимым:
    прятать записи из-за забытой строчки конфига нельзя (тот же принцип, что у групп списка).
    """
    state = request.app.state
    hub = getattr(state, "hub", None)
    order = {str(x.get("slug")): n for n, x in enumerate(hub.spaces)} if hub else {}
    items = []
    for slug, corpus in state.corpora.items():
        corpus.index.refresh_if_stale()
        brand = corpus.brand or {}
        items.append({
            "slug": slug,
            "title": brand.get("title") or slug,
            "tagline": brand.get("tagline") or "",
            "records_count": len(corpus.index),
            "hours": round(corpus.index.total_sec / 3600, 1),
            "accent": ((corpus.theme or {}).get("tokens") or {}).get("accent") or "",
            "chat_enabled": bool((corpus.chat or {}).get("enabled", True)),
        })
    items.sort(key=lambda i: order.get(i["slug"], len(order)))
    return {
        "hub": hub.public() if hub else None,
        "default": getattr(getattr(state, "default_corpus", None), "slug", None),
        "corpora": items,
    }


@router.get("/calendar")
async def calendar(request: Request, slug: str | None = None) -> dict:
    """Будущее пространства: ближайшие встречи и идеи — из `calendar.json` в каталоге корпуса.

    Файл производный: его пишет инструмент корпуса из внешнего источника (у кого календарь в
    Confluence, у кого таблица), BFF наружу не ходит и формы источника не знает. Файла нет —
    будущее пусто, и это не ошибка: прошлое календарь берёт из самих записей.
    """
    corpus = _corpus(request, slug)
    path = corpus.dir / "calendar.json"
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except ValueError:
            data = {}
    return {
        "corpus": corpus.slug,
        "source": str(data.get("source") or ""),
        "source_url": str(data.get("source_url") or ""),
        "generated": str(data.get("generated") or ""),
        "upcoming": list(data.get("upcoming") or []),
        "ideas": list(data.get("ideas") or []),
    }


@router.get("/records")
async def records(request: Request, slug: str | None = None) -> dict:
    corpus = _corpus(request, slug)
    corpus.index.refresh_if_stale()  # новые записи приезжают rsync'ом, без рестарта
    # ⚠️ Список ПЛОСКИЙ и отдаётся целиком: 188 записей это 156 КБ, и фильтры на клиенте
    # работают мгновенно и без сервера. Раздел, год, метка и спикер лежат полями каждой записи —
    # фасеты фронт считает сам, второго представления тех же данных заводить не надо.
    # Доехала ли запись до ПОИСКА. Индексация идёт плановым прогоном (с 24.09 — не на каждую
    # загрузку), поэтому свежая или только что поправленная запись читается, но ещё не ищется.
    # Молчать об этом нельзя: человек решит, что поиск сломан.
    indexed_at = _indexed_at(request, corpus.slug)
    return {
        "corpus": corpus.slug,
        "count": len(corpus.index),
        "records": [{**r.to_dict(), **({"indexed": r.mtime <= indexed_at} if indexed_at else {})}
                    for r in corpus.index.all()],
        # Направление чтения: общее и по разделам. Из данных его не вывести — курс читают
        # подряд, а митапы свежими сверху, и это решение владельца, а не свойство записей.
        "reading": {"default": corpus.index.order, "sections": corpus.index.section_order},
        "indexed_at": indexed_at,
    }


def _indexed_at(request: Request, slug: str) -> float:
    """Когда в последний раз собирали индекс этого пространства — из отметки планового прогона.

    Нет отметки (или её не настроили) — возвращаем 0, и тогда сайт про индекс ничего не говорит:
    обещать «не в поиске» без знания о поиске хуже, чем молчать.
    """
    stamp = engine_for(request.app.state.cfg, slug).index_stamp
    if not stamp:
        return 0.0
    try:
        return float(json.loads(Path(stamp).read_text(encoding="utf-8")).get("at") or 0)
    except (OSError, ValueError, TypeError):
        return 0.0


@router.get("/brand/{name:path}")
async def brand_asset(request: Request, name: str, slug: str | None = None):
    """Картинки бренда корпуса: обложка, портреты докладчиков.

    Отдаём из каталога корпуса, а не из общей статики: доменные файлы живут
    рядом со своим конфигом.

    Кэш — с обязательной проверкой, а не «на сутки»: имя файла постоянное, и
    длинный кэш означает, что заменённый портрет посетитель увидит только
    завтра (ловили ровно это).

    Проверку отвечаем сами: FileResponse ставит ETag, но условные запросы не
    разбирает — без этого «перепроверь» означало бы «качай заново каждый раз».
    """
    corpus = _corpus(request, slug)
    path = corpus.brand_file(name)
    if path is None:
        raise HTTPException(404, "нет такого файла")

    cache = {"Cache-Control": "public, max-age=0, must-revalidate"}
    tag = _etag(path)
    if tag and tag in [v.strip() for v in (request.headers.get("if-none-match") or "").split(",")]:
        return Response(status_code=304, headers={**cache, "ETag": tag})
    return FileResponse(path, headers=cache)


def _etag(path) -> str:
    """Метка версии файла — как её считает Starlette, чтобы значения совпадали."""
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f'"{md5(f"{stat.st_mtime}-{stat.st_size}".encode(), usedforsecurity=False).hexdigest()}"'


@router.get("/records/{record_id}/words")
async def record_words(
    request: Request,
    record_id: str,
    slug: str | None = None,
    start: float | None = None,
    end: float | None = None,
    pad: int = 0,
) -> dict:
    """Слова записи с временами — для караоке и перемотки по фразе.

    Без `start`/`end` — вся запись (читалка). С ними — только звучащее в этом
    промежутке (карточка-момент): реплика бывает длиннее чанка, и без обрезки
    карточка на сорок секунд тянула бы четырёхминутный монолог.

    `pad` — сколько целых реплик добавить до и после: цитата вырезана из
    разговора, и понять её, не слыша вопроса, на который отвечают, часто нельзя.
    Растёт по нажатию «шире», поэтому и ограничен: это чтение контекста, а не
    способ выкачать запись по кусочкам.

    Отдельным запросом, а не полем в потоке ответа: цитат бывает под два
    десятка, карточки по умолчанию свёрнуты, и вшивать слова в каждую — сотни
    лишних килобайт на мобильном.
    """
    corpus = _corpus(request, slug)
    meta = _meta(corpus, record_id)

    # путь ДО расшифровки: имя файла слов знает words.py (`record.md` → `record.words.json`),
    # и второго места, где это знание записано, быть не должно
    turns = words.load(corpus.index.path_of(meta))
    if start is not None and end is not None:
        turns = words.context_between(turns, start, end, max(0, min(pad, _MAX_PAD)))
    return {
        "record": meta.id,
        "duration_sec": meta.duration_sec,
        # запись может быть ещё не выровнена — клиент тогда честно откатится
        # на подсветку по репликам, а не покажет пустую расшифровку
        "aligned": bool(turns),
        "turns": [t.to_dict() for t in turns],
    }


@router.get("/records/{record_id}/transcript.md")
async def record_transcript(request: Request, record_id: str, slug: str | None = None):
    """Расшифровка как есть — её и скачивает посетитель."""
    corpus = _corpus(request, slug)
    meta = _meta(corpus, record_id)
    path = corpus.index.path_of(meta)
    if not path.is_file():
        raise HTTPException(404, "файл расшифровки не найден")
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{corpus.slug}-{meta.id}.md",
    )


@router.get("/records/{record_id}/slides.pdf")
async def record_slides(request: Request, record_id: str, slug: str | None = None):
    """Слайды доклада: лежат в каталоге записи, имя файла — из шапки.

    Отдаём инлайн, а не файлом на скачивание: их смотрят прямо в браузере рядом с расшифровкой.
    """
    corpus = _corpus(request, slug)
    meta = _meta(corpus, record_id)
    path = corpus.index.file_of(meta, meta.slides) if meta.slides else None
    if path is None:
        raise HTTPException(404, "у записи нет слайдов")
    return FileResponse(path, media_type="application/pdf")


@router.get("/records/{record_id}/frame/{name:path}")
async def record_frame(request: Request, record_id: str, name: str, slug: str | None = None):
    """Кадр экрана из каталога записи (`slides/sNNN.jpg`): обложка карточки, позже — кадр у
    момента и таймлайн. Белый список, а не чёрный: только `slides/*.jpg` без подкаталогов, и
    только внутри каталога записи (`file_of` проверяет путь). `{name:path}` — в имени есть слэш.
    Кэш — как у бренда: имя кадра постоянно, а сам кадр меняется при пересборке шкалы, поэтому
    «перепроверь», а не «на сутки»."""
    corpus = _corpus(request, slug)
    meta = _meta(corpus, record_id)
    tail = name[len("slides/"):] if name.startswith("slides/") else ""
    if not tail or "/" in tail or not tail.endswith(".jpg"):
        raise HTTPException(404, "нет такого кадра")
    path = corpus.index.file_of(meta, name)
    if path is None:
        raise HTTPException(404, "нет такого кадра")
    cache = {"Cache-Control": "public, max-age=0, must-revalidate"}
    tag = _etag(path)
    if tag and tag in [v.strip() for v in (request.headers.get("if-none-match") or "").split(",")]:
        return Response(status_code=304, headers={**cache, "ETag": tag})
    return FileResponse(path, media_type="image/jpeg", headers=cache)


@router.get("/records/{record_id}/frames")
async def record_frames(request: Request, record_id: str, slug: str | None = None):
    """Кадры экрана записи для слайдшоу на карточке (владелец, 14.09): имена кадров слайдов по
    времени, БЕЗ кадров с людьми, и рамка обрезки обложки (`cover.crop.box`, доли исходного кадра:
    полоса миниатюр участников и подпись говорящего у записи одна на все кадры — фронт режет
    сырые кадры той же рамкой). Читается сайдкар по запросу — список записей от этого не пухнет."""
    import json
    corpus = _corpus(request, slug)
    meta = _meta(corpus, record_id)
    path = corpus.index.file_of(meta, "record.slides.json")
    if path is None:
        return {"frames": [], "crop": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"frames": [], "crop": None}
    frames = []
    for s in data.get("slides") or []:
        frame = str(s.get("frame") or "")
        # чужие лица (зал, галерея) в слайдшоу не попадают; докладчик (`who: speaker`) — можно
        if (s.get("people") and s.get("who") != "speaker") or not frame.startswith("slides/") or not frame.endswith(".jpg"):
            continue
        if corpus.index.file_of(meta, frame) is not None:
            frames.append({"frame": frame, "t0": s.get("t0"), "title": ((s.get("desc") or {}).get("title") or "")[:80]})
    box = ((data.get("cover") or {}).get("crop") or {}).get("box")
    return {"frames": frames, "crop": box if isinstance(box, list) and len(box) == 4 else None}


@router.get("/media/{name:path}")
async def media(request: Request, name: str, slug: str | None = None):
    """Видео записи из локального каталога пространства.

    ⚠️ `{name:path}`, а не `{name}`. У перенесённых записей в шапке лежит ПУТЬ внутри архива
    («Каталог/Подкаталог/файл.mp4»), а `{name}` слэш не берёт: маршрут молча не совпадал,
    запрос уходил в SPA-заглушку, и <video> получал HTML с кодом 200. Проверка «внутри ли
    каталога» от этого не слабеет — она смотрит РАЗРЕШЁННЫЙ путь, а не строку имени.

    Когда задан `media_base`, фронт сюда не ходит вовсе и берёт файл прямо из архива.

    В проде медиа раздаёт Caddy напрямую: гонять гигабайтные файлы через питон-процесс, в
    котором крутится агентский цикл, незачем. FileResponse умеет Range, поэтому перемотка в
    деве работает — иначе караоке нечем было бы проверять.
    """
    corpus = _corpus(request, slug)
    path = corpus.media_file(name)
    if path is None:
        raise HTTPException(404, "нет такого файла")
    return FileResponse(path)


@router.post("/feedback")
async def feedback(request: Request, payload: Feedback) -> dict:
    """Оценка — отдельной строкой журнала: jsonl не переписываем, join по answer_id."""
    await request.app.state.journal.write(
        {
            "type": "vote",
            "answer_id": payload.answer_id,
            "vote": payload.vote,
            "comment": payload.comment[:2000],
        }
    )
    return {"ok": True}


@router.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    limiter = state.limiter
    return {
        "status": "ok",
        "corpora": sorted(state.corpora),
        "records": {slug: len(c.index) for slug, c in state.corpora.items()},
        "hours": {slug: round(c.index.total_sec / 3600, 1) for slug, c in state.corpora.items()},
        # Куда ходит каждое пространство за ответом. Без этого «поиск молчит» приходится
        # выяснять сравнением конфигов на машине.
        "engines": {slug: c.cfg.base_url for slug, c in getattr(state, "engines", {}).items()},
        "streams_free": state.gate.free,
        "streams_limit": state.gate.limit,
        # Дневной расход — чтобы владелец видел, близко ли до потолка, не заходя на машину.
        "asked_today": limiter.used_today,
        "daily_limit": limiter.daily_total if limiter.enabled else 0,
    }
