"""Превью ссылок в мессенджерах.

Главное здесь — то, чего не видно глазами: мессенджер скачивает страницу сервером
и НЕ выполняет JS, поэтому если мета-теги не подставились или подставились криво,
ссылка молча разворачивается в безликую карточку сайта.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app import meta  # noqa: E402

INDEX = """<!doctype html>
<html><head>
<title>morag audio</title>
<meta charset="utf-8">
</head><body><div id="app"></div></body></html>"""


def test_теги_встают_в_head_и_старый_title_уходит():
    tags = meta.build_tags(title="Выпуск №2-9 · 20:34", description="О чём речь", url="https://x/y")
    page = meta.inject(INDEX, tags)
    assert page.count("<title>") == 1, "два заголовка — бот возьмёт первый попавшийся"
    assert "Выпуск №2-9 · 20:34" in page
    assert page.index("og:title") < page.index("</head>")


def test_кавычки_и_угловые_скобки_экранируются():
    """Заголовок выпуска — чужой текст; без экранирования он ломает разметку."""
    tags = meta.build_tags(
        title='Про "кавычки" и <script>alert(1)</script>',
        description="a & b",
        url="https://x/y",
    )
    assert "<script>" not in tags
    assert "&lt;script&gt;" in tags
    assert '"кавычки"' not in tags and "&quot;" in tags


def test_описание_подрезается_и_не_рвёт_слово():
    long = "слово " * 100
    tags = meta.build_tags(title="t", description=long, url="u")
    line = next(x for x in tags.split("\n") if 'name="description"' in x)
    assert len(line) < 300 and "…" in line


class FakeRecord:
    id = "2026-03-12-kafka"
    title = "Kafka без боли: как мы пережили миграцию"
    date = "2026-03-12"
    group = "Backend-митап FTC #17"
    speakers = ["Аня Петрова", "Пётр Аннин"]


class FakeCorpus:
    slug = "demo"
    brand = {"title": "Записи митапов"}


def test_описание_записи_несёт_тайм_код_и_спикеров():
    title, description = meta.describe_record(FakeCorpus(), FakeRecord(), 1234)
    assert "20:34" in title, "без тайм-кода ссылка «на место» неотличима от ссылки на запись"
    assert "Аня Петрова" in description
    assert "Backend-митап FTC #17" in description, "по митапу видно, о какой встрече речь"


def test_описание_записи_без_секунды_не_врёт_про_время():
    title, _ = meta.describe_record(FakeCorpus(), FakeRecord(), None)
    assert "20:34" not in title and " · 0:00" not in title
