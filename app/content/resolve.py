"""Цитата движка → запись и секунда.

Метку (`source.name`) парсить нельзя: это заголовок доклада, в нём может быть что угодно.
Надёжный ключ один — внутренний doc_id движка `local:<источник>:<путь>[#секунда]`
(`local:demo:2026-03-12-kafka/record.md#123`): он несёт путь файла и секунду.

⚠️ Форму пути мы НЕ знаем и знать не должны: у подкаста здесь стояла регулярка
`season\\d+/ep\\d+\\.md`, и она же была причиной, по которой чужой корпус в этот код не влезал.
Путь просто отдаётся индексу — тот скажет, есть ли такая запись. Поэтому doc_id от слайдов
(`.../slides.pdf`) не ломается, а честно даёт «записи нет».
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .records import RecordIndex

# Media Fragments допускает и `npt:`, и дробные секунды.
# ⚠️ У наших записей в шапке нет поля `url` вовсе (видео раздаём сами), так что сюда приходит
# пусто, и секунда берётся из doc_id. Оставлено на случай, если движок не положит её в doc_id —
# замер на живом pipelines делается в вехе 3, и тогда либо эта функция уйдёт, либо в шапку
# вернётся `url:`. Гадать не будем.
_FRAGMENT_RE = re.compile(r"(?:^|[#&])t=(?:npt:)?(\d+(?:\.\d+)?)")

_FALLBACK_LEN = 30  # если границу вычислить не удалось


def parse_seconds(url: str) -> int | None:
    m = _FRAGMENT_RE.search(url or "")
    return int(float(m.group(1))) if m else None


def parse_doc_id(doc_id: str) -> tuple[str, int | None]:
    """`local:<источник>:<путь>[#сек]` → (путь, секунда). Ничего не знает о форме пути."""
    body = (doc_id or "").strip()
    parts = body.split(":", 2)
    if len(parts) == 3 and parts[0] == "local":
        body = parts[2]
    path, _, fragment = body.partition("#")
    sec: int | None = None
    if fragment:
        try:
            sec = int(float(fragment))
        except ValueError:
            sec = None
    return path.strip(), sec


def resolve(url: str, index: RecordIndex, doc_id: str = "") -> tuple[str | None, int | None]:
    """(record_id, sec). Не нашли запись — не ошибка: цитата всё равно читается текстом."""
    sec = parse_seconds(url)
    record_id = None

    if doc_id:
        path, doc_sec = parse_doc_id(doc_id)
        if path:
            meta = index.by_path(path)
            if meta:
                record_id = meta.id
        if sec is None:
            sec = doc_sec

    return record_id, sec


def chunk_end(index: RecordIndex, record_id: str | None, text: str, start: int | None) -> int | None:
    """Конец процитированного куска — чтобы карточка показывала его целиком, а не первые 30 с.

    Тайм-код движок печатает ТОЛЬКО у первой строки чанка, так что «последний
    тайм-код» ничего не даёт. Зато каждая строка чанка — ровно одна реплика:
    отсчитываем столько же реплик вперёд по своей расшифровке и берём их конец.
    """
    if start is None:
        return None
    meta = index.by_id(record_id) if record_id else None
    if meta is None:
        return start + _FALLBACK_LEN

    try:
        from .transcript import load_utterances, locate

        utterances = load_utterances(index.path_of(meta))
    except OSError:
        return start + _FALLBACK_LEN

    pos = locate(utterances, start)
    if pos < 0 or not utterances:
        return start + _FALLBACK_LEN

    spoken = sum(1 for line in (text or "").splitlines() if line.strip().startswith("["))
    last = min(len(utterances) - 1, pos + max(1, spoken) - 1)
    return max(int(utterances[last].end_sec), start + 1)


def media_url(meta, slug: str = "", base: str = "") -> str:
    """Адрес видео записи. Строим МЫ, а не движок: в шапке записи ссылки нет вовсе —
    там имя файла, а кто и по какому пути его раздаёт, знает только сайт.

    ⚠️ Это ВТОРОЕ место, где собирается адрес медиа (первое — `mediaUrl` во фронте). Разойдутся
    — карточка-момент в ответе перестанет проигрываться, а читалка будет работать, и искать
    причину придётся в чате. Форма обязана совпадать байт в байт.

    Слаг пространства подставляем всегда, даже когда оно одно: у подкаста ровно на этом
    сломалась мультиинстансность — фронт не слал `?slug=`, и второе пространство получало бы
    медиа первого."""
    if not getattr(meta, "media", ""):
        return ""
    # `quote` по умолчанию не трогает `/`, поэтому путь внутри архива остаётся путём,
    # а кириллица и пробелы в сегментах кодируются.
    path = quote(meta.media)
    if base:
        return f"{base.rstrip('/')}/{path}"
    query = f"?slug={quote(slug)}" if slug else ""
    return f"/api/media/{path}{query}"


def make_resolver(index: RecordIndex, slug: str = "", media_base: str = ""):
    """Резолвер в том виде, в каком его ждёт engine/normalize.py.

    Четвёртым значением отдаём адрес видео: у цитаты движка `url` пуст (внешнего медиа у
    нас нет), а карточке-моменту нужно что-то, что можно включить.
    """

    def _resolver(url: str, doc_id: str = "", text: str = "") -> tuple:
        record_id, sec = resolve(url, index, doc_id)
        meta = index.by_id(record_id) if record_id else None
        return record_id, sec, chunk_end(index, record_id, text, sec), media_url(meta, slug, media_base) if meta else ""

    return _resolver
