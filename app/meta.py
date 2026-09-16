"""Превью ссылок в мессенджерах (Open Graph).

Зачем отдельный слой: Telegram, WhatsApp и прочие разворачивают ссылку, скачивая
страницу СЕРВЕРОМ и не выполняя JS. Наше приложение рисует всё на клиенте, поэтому
без подстановки мета-тегов любая ссылка выглядит одинаково — «сайт записей»,
без доклада, тайм-кода и темы разговора. Ради этого путь и делался красивым:
в хеше (`#/ep/…`) сервер вообще не видит, куда ведёт ссылка.

Подставляем в готовый index.html, а не рендерим свою страницу: разметка одна,
и расхождение между «что видит бот» и «что видит человек» невозможно.
"""

from __future__ import annotations

import html
import re

_HEAD_END = re.compile(r"</head>", re.IGNORECASE)


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,.;:—-") + "…"


def _tag(prop: str, value: str, *, name: bool = False) -> str:
    attr = "name" if name else "property"
    return f'<meta {attr}="{prop}" content="{html.escape(value, quote=True)}">'


def build_tags(
    *,
    title: str,
    description: str,
    url: str,
    image: str = "",
) -> str:
    # Режем здесь, а не только в описателях: длина приходит из чужого текста
    # (заголовок доклада, тема разговора, `about` из конфига корпуса), и тег
    # на шестьсот символов боты обрезают сами — но по своему усмотрению.
    title = _clip(title, 120)
    description = _clip(description, 200)
    tags = [
        f"<title>{html.escape(title)}</title>",
        _tag("description", description, name=True),
        _tag("og:type", "website"),
        _tag("og:title", title),
        _tag("og:description", description),
        _tag("og:url", url),
        _tag("twitter:card", "summary_large_image" if image else "summary", name=True),
    ]
    if image:
        tags.append(_tag("og:image", image))
    return "\n".join(tags)


def inject(index_html: str, tags: str) -> str:
    """Кладём наши теги перед </head>, чтобы они перебили дефолтные из шаблона."""
    if not tags:
        return index_html
    # Свой <title> в шаблоне убираем: два тега подряд — и боты берут первый.
    cleaned = re.sub(r"<title>.*?</title>\s*", "", index_html, count=1, flags=re.DOTALL | re.IGNORECASE)
    return _HEAD_END.sub(tags + "\n</head>", cleaned, count=1)


def describe_record(corpus, record, sec: float | None) -> tuple[str, str]:
    """Заголовок и описание для ссылки на место в записи."""
    brand = (corpus.brand or {}).get("title") or corpus.slug
    title = getattr(record, "title", "") or getattr(record, "id", "")
    if sec:
        title = f"{title} · {int(sec) // 60}:{int(sec) % 60:02d}"
    speakers = getattr(record, "speakers", None) or []
    parts = []
    if speakers:
        parts.append(", ".join(speakers[:4]))
    # митап и дата: по ним в мессенджере понятно, о какой встрече речь
    for extra in (getattr(record, "group", ""), getattr(record, "date", "")):
        if extra:
            parts.append(extra)
    description = " · ".join(parts) or f"Расшифровка записи с тайм-кодами · {brand}"
    return _clip(title, 120), _clip(description, 200)

