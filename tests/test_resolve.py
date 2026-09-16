"""Цитата движка → запись и секунда.

Ради этого модуля переписан весь резолв: у подкаста запись искалась по mp3-ссылке и по
регулярке `season\\d+/ep\\d+\\.md`, то есть код знал форму пути чужого корпуса. Здесь путь
просто отдаётся индексу — и поэтому doc_id от слайдов не ломает ответ, а честно даёт «не нашли».
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.content.records import RecordIndex  # noqa: E402
from app.content.resolve import (  # noqa: E402
    chunk_end,
    make_resolver,
    media_url,
    parse_doc_id,
    parse_seconds,
    resolve,
)

RECORD = """---
title: "Kafka без боли"
date: "2026-03-12"
media: "2026-03-12-kafka.mp4"
duration_sec: 300
---

[Аня] <!-- t:0.0 --> Первая реплика.

[Аня] <!-- t:120.0 --> Вторая реплика.

[Петя] <!-- t:150.0 --> Третья реплика.
"""


@pytest.fixture
def index(tmp_path: Path) -> RecordIndex:
    directory = tmp_path / "2026-03-12-kafka"
    directory.mkdir()
    (directory / "record.md").write_text(RECORD, encoding="utf-8")
    return RecordIndex(tmp_path)


@pytest.mark.parametrize(
    "doc_id,expected",
    [
        ("local:demo:2026-03-12-kafka/record.md#123", ("2026-03-12-kafka/record.md", 123)),
        ("local:demo:2026-03-12-kafka/record.md", ("2026-03-12-kafka/record.md", None)),
        # дробная секунда: движок печатает и такие
        ("local:demo:2026-03-12-kafka/record.md#123.7", ("2026-03-12-kafka/record.md", 123)),
        # чужая форма пути разбирается ровно так же — знания о ней у нас нет
        ("local:podcast:season2/ep5.md#2227", ("season2/ep5.md", 2227)),
        ("", ("", None)),
    ],
)
def test_parse_doc_id(doc_id: str, expected):
    assert parse_doc_id(doc_id) == expected


def test_resolve_finds_the_record_by_doc_id(index: RecordIndex):
    assert resolve("", index, "local:demo:2026-03-12-kafka/record.md#123") == ("2026-03-12-kafka", 123)


def test_slides_do_not_break_the_answer(index: RecordIndex):
    """doc_id от PDF — не ошибка: цитата просто читается текстом, без ссылки в читалку."""
    assert resolve("", index, "local:demo:2026-03-12-kafka/slides.pdf") == (None, None)


def test_screen_document_resolves_to_its_record(index: RecordIndex):
    """`slides.md` — документ экрана записи (что было на экране, по секундам): его цитата
    открывает ту же читалку на той же секунде. Иначе карточка-момент с экрана молча теряла бы
    видео — а ради этого перехода документ и заведён."""
    assert resolve("", index, "local:demo:2026-03-12-kafka/slides.md#558") == ("2026-03-12-kafka", 558)
    assert resolve("", index, "local:demo:нет-такой/slides.md#5") == (None, 5)


def test_unknown_record_is_not_an_error(index: RecordIndex):
    assert resolve("", index, "local:demo:нет-такой/record.md#5") == (None, 5)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("/api/media/a.mp4#t=2227", 2227),
        ("/api/media/a.mp4#t=2227.5", 2227),
        ("/api/media/a.mp4#t=npt:90", 90),
        ("/api/media/a.mp4", None),
        ("", None),
    ],
)
def test_parse_seconds(url: str, expected):
    """Секунду из адреса всё ещё умеем читать: у наших записей `url` пуст, но если
    окажется, что движок не кладёт её в doc_id, в шапку вернётся `url:` (веха 3)."""
    assert parse_seconds(url) == expected


def test_media_url_carries_the_corpus_slug(index: RecordIndex):
    """Слаг подставляем всегда: у подкаста ровно на этом сломалась мультиинстансность —
    фронт не слал `?slug=`, и второй корпус получал бы медиа первого."""
    meta = index.by_id("2026-03-12-kafka")
    assert media_url(meta, "demo") == "/api/media/2026-03-12-kafka.mp4?slug=demo"


def test_resolver_gives_the_card_something_to_play(index: RecordIndex):
    """Четвёртое значение — адрес видео: у цитаты движка `url` пуст, а карточке-моменту
    нужно что-то, что можно включить."""
    resolver = make_resolver(index, "demo")
    record_id, sec, end, media = resolver("", "local:demo:2026-03-12-kafka/record.md#120", "")

    assert (record_id, sec) == ("2026-03-12-kafka", 120)
    assert media == "/api/media/2026-03-12-kafka.mp4?slug=demo"
    assert end and end > sec


def test_chunk_end_covers_all_quoted_lines(index: RecordIndex):
    """Волна в карточке рисуется по длине чанка: конец = конец последней его реплики."""
    text = "[2:00] [Аня] Вторая реплика.\n[Петя] Третья реплика."
    assert chunk_end(index, "2026-03-12-kafka", text, 120) == 300  # последняя тянется до конца

    # запись неизвестна — не падаем, отдаём разумную оценку
    assert chunk_end(index, None, text, 120) == 150


# --- вложенная раскладка ----------------------------------------------------


def test_nested_record_resolves_by_engine_doc_id(tmp_path: Path):
    """Запись, лежащая по ветке и году, находится по doc_id движка.

    ⓘ Имена веток здесь СИНТЕТИЧЕСКИЕ («Рубрика», «Ветка»): каталог тестов проверяется тем же
    гейтом универсальности, что и фронт, — он однажды переедет в публичный репозиторий, и
    названия рубрик компании туда не едут. Проверяем глубину пути, а не конкретное имя.

    ⚠️ Ради этого случая тест и написан. `RecordMeta.path` считается ОТ КОРНЯ индекса, а движок
    называет документ от своего `sources[].path` — и оба обязаны дать одну строку
    `Рубрика/2024/<id>/record.md`. Сделай ветку отдельным корнем индекса — сайт посчитает путь
    от ветки (`2024/<id>/record.md`), движок от `records/`, и `by_path` МОЛЧА перестанет
    совпадать: ответы останутся, а переход из цитаты в читалку исчезнет.
    """
    directory = tmp_path / "Рубрика" / "2024" / "2026-03-12-kafka"
    directory.mkdir(parents=True)
    (directory / "record.md").write_text(RECORD, encoding="utf-8")
    index = RecordIndex(tmp_path)

    assert len(index) == 1, "рекурсивный обход не нашёл вложенную запись"
    meta = index.by_id("2026-03-12-kafka")
    assert meta is not None, "id — имя ЛИСТОВОГО каталога, глубина на него не влияет"
    assert meta.path == "Рубрика/2024/2026-03-12-kafka/record.md"

    doc_id = f"local:demo:{meta.path}#123"
    record_id, sec = resolve("", index, doc_id)
    assert (record_id, sec) == ("2026-03-12-kafka", 123)


def test_nested_and_flat_records_live_together(tmp_path: Path):
    """Перекладка идёт не мгновенно: часть записей уже в ветках, часть ещё плоско."""
    for path in (tmp_path / "Ветка" / "2025" / "concert", tmp_path / "flat-one"):
        path.mkdir(parents=True)
        (path / "record.md").write_text(RECORD, encoding="utf-8")
    index = RecordIndex(tmp_path)
    assert {m.id for m in index.all()} == {"concert", "flat-one"}
    assert index.by_path("Ветка/2025/concert/record.md") is not None
    assert index.by_path("flat-one/record.md") is not None


# ---------------------------------------------------------------------------
# Сторожа приёмки: чем ловится поломка разворота цитаты в запись
# ---------------------------------------------------------------------------

def test_сторож_ловит_цитату_которая_не_разворачивается_в_запись():
    """⚠️ Единственная проверка, ловящая поломку САМОГО ЦЕННОГО в ответе — перехода из цитаты к
    секунде видео.

    Остальные метрики приёмки (число цитат, якоря [N], протечки Speaker_N) остаются зелёными,
    даже если ни одна карточка больше не разрешается: ответ читается, источники перечислены, и
    только кликнуть некуда. Именно так выглядела бы смена формы `doc_id` в поле `source`.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import ask_probe

    # ⚠️ Запись берём С ДИСКА, а не литералом: во-первых, гейт универсальности запрещает
    # доменные названия в тестах, во-вторых, литерал протух бы при первом же переносе записи.
    # Корпус — тот, на который указывает конфиг (в тестах — демо), пространство — его первое.
    import spaces
    slug = spaces.slugs()[0]
    records = spaces.records_dir(slug)
    some = next(records.rglob("record.md"), None)
    if some is None:
        pytest.skip("в корпусе нет записей — проверять нечего")
    real = "local:x:" + str(some.relative_to(records)) + "#247"
    fake = "local:x:" + "нет-такой-записи/record.md#5"
    bad = ask_probe.unresolved_citations([{"doc": real}, {"doc": fake}], slug)
    assert len(bad) == 1 and "нет-такой-записи" in bad[0]
    assert ask_probe.unresolved_citations([{"doc": real}], slug) == []


def test_сторож_ловит_номер_без_карточки():
    """Номер, поставленный в текст, но не подкреплённый цитатой: на фронте остаётся простым
    текстом — выглядит ссылкой и никуда не ведёт. Проверка «нет ни одной ссылки» этого не видит,
    потому что она всё-или-ничего."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import ask_probe

    citations = [{"n": 1}, {"n": 3}]
    assert ask_probe.orphan_anchors("вывод [1] и ещё [42].", citations) == [42]
    assert ask_probe.orphan_anchors("вывод [1][3].", citations) == []
