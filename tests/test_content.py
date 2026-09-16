"""Тесты индекса и разбора расшифровок — на реальном корпусе, без сети."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.content.frontmatter import read_document, read_frontmatter  # noqa: E402
from app.content.records import RecordIndex  # noqa: E402
from app.content.transcript import (  # noqa: E402
    UTTERANCE_RE,
    load_utterances,
    parse_utterances,
    window,
)

UNNAMED = re.compile(r"^Speaker_\d+$")

sys.path.insert(0, str(REPO / "tools"))
import spaces  # noqa: E402


def record_roots() -> list[Path]:
    """Корни записей, на которых проверяются инварианты.

    Рабочий корпус, если он рядом; иначе — демо. Оба варианта настоящие: в чистой репе
    веб-морды корпуса компании не будет вовсе, и проверки обязаны там работать, а на машине
    владельца — идти по всем 167 записям, а не по трём синтетическим.
    """
    live = [d for d in spaces.records_dirs() if d.is_dir()]
    return live or [REPO / "corpora" / "demo" / "records"]


@pytest.fixture(scope="module")
def index() -> RecordIndex:
    return RecordIndex(record_roots())


def md_files() -> list[Path]:
    # ⚠️ Рекурсивно: записи лежат по веткам и годам. С шаблоном на один уровень список
    # становится ПУСТЫМ, и половина тестов файла зеленеет вхолостую — см. страховку ниже.
    return sorted(f for root in record_roots() for f in root.glob("**/record.md"))


def make_corpus(root: Path, records: dict[str, str]) -> RecordIndex:
    """Корпус из шапок: `{id: текст записи}`. Тело можно не писать."""
    for name, text in records.items():
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "record.md").write_text(text, encoding="utf-8")
    return RecordIndex(root)


HEAD = '---\ntitle: "{title}"\ndate: "{date}"\n{extra}---\n\n[Кто-то] <!-- t:0.0 --> Слова.\n'


def head(title="Доклад", date="2026-03-12", **extra) -> str:
    lines = "".join(f"{k}: {v}\n" for k, v in extra.items())
    return HEAD.format(title=title, date=date, extra=lines)


# --- шапки -----------------------------------------------------------------


def test_corpus_present():
    # Не фиксируем точное число: корпус растёт с каждым митапом, и тест,
    # который надо править после каждой транскрибации, только мешает.
    assert md_files(), "в корпусе нет ни одной записи"


@pytest.mark.parametrize("path", md_files(), ids=lambda p: p.parent.name)
def test_frontmatter_has_required_keys(path: Path):
    """Обязательны ровно два поля. Остальное опционально принципиально: запись без
    видео (перенесённый с прежнего сайта анонс) обязана показываться, а не выпадать."""
    fm = read_frontmatter(path)
    assert str(fm.get("title") or "").strip(), f"нет title в {path}"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(fm.get("date") or "")), f"нет даты в {path}"


# --- индекс ----------------------------------------------------------------


def test_index_sees_every_directory(index: RecordIndex):
    # ⚠️ Сначала непустота, потом равенство. Без первой строки тест доказывал бы `0 == 0`:
    # ровно так он и остался бы зелёным, когда раскладка ушла на уровень глубже, а обход — нет.
    assert md_files(), "обход не нашёл ни одной записи — сравнивать нечего"
    assert len(index) == len(md_files())


def test_id_is_the_directory_name(index: RecordIndex):
    """Главное решение модели контента: каталог уникален по построению ФС, поэтому
    id не нужно ни выдумывать, ни хранить в шапке — и он же адрес страницы."""
    for path in md_files():
        assert index.by_id(path.parent.name) is not None


def test_fresh_records_first(tmp_path: Path):
    index = make_corpus(tmp_path, {
        "2026-01-10-старая": head(date="2026-01-10"),
        "2026-05-30-свежая": head(date="2026-05-30"),
    })
    assert [r.id for r in index.all()] == ["2026-05-30-свежая", "2026-01-10-старая"]


def test_record_without_title_or_date_is_skipped(tmp_path: Path):
    """Битая запись не должна ронять весь раздел — её просто не видно."""
    index = make_corpus(tmp_path, {
        "целая": head(),
        "без-даты": '---\ntitle: "Есть"\n---\n\n[Кто] <!-- t:0.0 --> Текст.\n',
        "без-заголовка": '---\ndate: "2026-03-12"\n---\n\n[Кто] <!-- t:0.0 --> Текст.\n',
    })
    assert [r.id for r in index.all()] == ["целая"]


def test_every_record_is_listed_even_without_a_group(tmp_path: Path):
    """Список отдаётся ПЛОСКИМ и не теряет записей, у которых поля группировки нет вовсе.

    У перенесённого с прежнего сайта анонса митапа не будет, и выпасть из списка он не имеет права:
    невидимую запись не ищут и не чинят.
    """
    index = make_corpus(tmp_path, {
        "2026-03-12-первый": head(date="2026-03-12", event='"Backend-митап #17"'),
        "2026-04-01-один": head(date="2026-04-01", event='"Data-митап #3"'),
        "2026-02-01-сирота": head(date="2026-02-01"),
    })

    assert [r.id for r in index.all()] == ["2026-04-01-один", "2026-03-12-первый", "2026-02-01-сирота"]
    assert [r.group for r in index.all()] == ["Data-митап #3", "Backend-митап #17", ""]


def test_group_field_is_configurable(tmp_path: Path):
    """Ось группировки — обычное поле шапки, имя поля задаёт site.yml."""
    (tmp_path / "запись").mkdir()
    (tmp_path / "запись" / "record.md").write_text(head(course='"Курс по Kafka"'), encoding="utf-8")

    assert RecordIndex(tmp_path, group_by="course").all()[0].group == "Курс по Kafka"
    assert RecordIndex(tmp_path, group_by="event").all()[0].group == ""


def test_serialization_has_no_local_path(index: RecordIndex):
    payload = index.all()[0].to_dict()
    assert "path" not in payload  # локальные пути наружу не отдаём
    assert set(payload) >= {"id", "title", "date", "group", "speakers", "duration_sec", "media",
                            # люди по ролям и счётчик голосов, формат и отметка — с 12.09
                            "participants", "voices", "kind", "award", "discussion",
                            "category", "topics", "year",
                            # обложка (кадр слайда) и сводка для карточки — с 13.09
                            "cover", "blurb"}


def test_refresh_picks_up_a_new_record(tmp_path: Path):
    """Записи приезжают на сервер rsync'ом — BFF обязан заметить их без рестарта."""
    index = make_corpus(tmp_path, {"первая": head()})
    assert len(index) == 1

    (tmp_path / "вторая").mkdir()
    (tmp_path / "вторая" / "record.md").write_text(head(title="Вторая"), encoding="utf-8")
    index.refresh_if_stale()

    assert len(index) == 2


def test_slides_are_taken_from_the_record_directory(tmp_path: Path):
    index = make_corpus(tmp_path, {"доклад": head(slides='"slides.pdf"')})
    meta = index.by_id("доклад")
    (tmp_path / "доклад" / "slides.pdf").write_bytes(b"%PDF-1.4")

    assert index.file_of(meta, meta.slides).name == "slides.pdf"
    # имя приходит из данных, поэтому наружу каталога записи не выпускаем
    assert index.file_of(meta, "../../../etc/passwd") is None


def test_cover_and_blurb_come_from_sidecars_and_refresh_without_touching_record_md(tmp_path: Path):
    """Обложка — `cover.frame` из record.slides.json (только `slides/*.jpg`, и файл обязан лежать
    рядом), сводка — `blurb.text` из record.meta.json. Оба пишут инструменты, не касаясь
    record.md, поэтому сторож свежести смотрит и на сайдкары."""
    import json
    import os
    index = make_corpus(tmp_path, {"доклад": head(), "анонс": head(title="Анонс")})
    rec = tmp_path / "доклад"
    (rec / "slides").mkdir()
    (rec / "slides" / "s003.jpg").write_bytes(b"\xff\xd8\xff")
    (rec / "record.slides.json").write_text(json.dumps({"cover": {"frame": "slides/s003.jpg", "n": 3}}), encoding="utf-8")
    (rec / "record.meta.json").write_text(json.dumps({"blurb": {"text": "О чём запись."}}), encoding="utf-8")
    stamp = os.stat(rec / "record.slides.json").st_mtime + 5
    os.utime(rec / "record.slides.json", (stamp, stamp))
    index.refresh_if_stale()                       # record.md не менялся — но сайдкар новее
    meta = index.by_id("доклад")
    assert meta.cover == "slides/s003.jpg" and meta.blurb == "О чём запись."
    assert index.by_id("анонс").cover == "" and index.by_id("анонс").blurb == ""
    assert index.file_of(meta, meta.cover).name == "s003.jpg"
    # чужой путь и кадр без файла не проходят: имя уезжает во фронт и вернётся запросом
    for bad in ("../../etc/passwd.jpg", "slides/../record.md", "slides/x/s1.jpg", "slides/s009.jpg", "record.md"):
        (rec / "record.slides.json").write_text(json.dumps({"cover": {"frame": bad}}), encoding="utf-8")
        index.build()
        assert index.by_id("доклад").cover == "", bad
    (rec / "record.slides.json").write_text("{битый json", encoding="utf-8")
    index.build()
    assert index.by_id("доклад").blurb == "О чём запись." and index.by_id("доклад").cover == ""


# --- реплики ---------------------------------------------------------------


@pytest.mark.parametrize("path", md_files(), ids=lambda p: p.parent.name)
def test_every_body_line_parses(path: Path):
    """Ни одна строка тела не должна ускользнуть от регулярки."""
    _, body = read_document(path)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    bad = [ln for ln in lines if not UTTERANCE_RE.match(ln)]
    assert not bad, f"{path}: {len(bad)} строк не разобрано, первая: {bad[0][:80]}"


def test_utterances_are_ordered_and_bounded(index: RecordIndex):
    record = index.all()[0]
    utterances = load_utterances(index.path_of(record))
    assert len(utterances) > 1
    assert all(a.sec <= b.sec for a, b in zip(utterances, utterances[1:]))
    assert all(u.end_sec >= u.sec for u in utterances)
    assert utterances[-1].end_sec == record.duration_sec


def test_named_speakers_match_frontmatter(index: RecordIndex):
    """Безымянные `Speaker_N` в шапку не идут — это решение `make_record.py`, а каждый
    названный голос тела обязан стоять в одном из двух списков людей: выступавшие или
    участники. Роль — дело сборки; здесь проверяется только, что никто не потерян."""
    for record in index.all():
        spoken = {u.speaker for u in load_utterances(index.path_of(record))}
        named = {s for s in spoken if not UNNAMED.match(s)}
        people = set(record.speakers) | set(record.participants)
        assert named <= people, f"{record.id}: в теле есть голос, которого нет ни в одном списке шапки"
        assert record.voices >= len(spoken), f"{record.id}: голосов в шапке меньше, чем в теле"


def test_locate_survives_rounded_timecode():
    """Снаружи секунды приходят целыми (#t=2227), а реплика стартует на 2227.4 —
    поиск «последняя не позже» промахнулся бы на одну реплику назад."""
    from app.content.transcript import locate

    utts = parse_utterances(
        "[А] <!-- t:10.0 --> первая\n\n[Б] <!-- t:2227.4 --> вторая\n\n[В] <!-- t:2300.0 --> третья",
        duration_sec=2400,
    )
    assert utts[locate(utts, 2227)].text == "вторая"
    assert utts[locate(utts, 2227.4)].text == "вторая"
    assert utts[locate(utts, 2280)].text == "вторая"  # середина реплики — она же
    assert utts[locate(utts, 2300)].text == "третья"


def test_window_around_second():
    utts = parse_utterances(
        "\n".join(f"[Имя{i}] <!-- t:{i * 10}.0 --> реплика {i}" for i in range(10)),
        duration_sec=100,
    )
    w = window(utts, sec=50, radius=2)
    assert [u.text for u in w] == [f"реплика {i}" for i in range(3, 8)]
    # у краёв окно просто короче, без падений
    assert len(window(utts, sec=0, radius=2)) == 3
    assert len(window(utts, sec=999, radius=2)) == 3


# --- разделы (два уровня) --------------------------------------------------
#
# Раздел — это КАТАЛОГ, а не вычисленный признак. Поэтому корпус тестов строится каталогами:
# `laid_out({"Летучка/2026/п": шапка})`. Раньше здесь жили правила `match`/`by`, и структуру
# считали два механизма — список правилами, диск раскладкой; теперь источник один.


def laid_out(root: Path, records: dict[str, str], **kw) -> RecordIndex:
    """Корпус, разложенный по каталогам: ключ — путь `<раздел>/<подраздел>/<id>` или просто `<id>`."""
    for name, text in records.items():
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "record.md").write_text(text, encoding="utf-8")
    return RecordIndex(root, group_by="event", **kw)


def test_section_and_subsection_come_from_the_directories(tmp_path: Path):
    """Раздел и подраздел — это каталоги над записью, как они лежат на диске.

    Ось второго уровня у каждого раздела СВОЯ: у летучки год, у курса курс. Полем шапки это не
    выразить — пришлось бы заводить общее поле под разную природу, — а каталог выражает даром,
    и его же видит движок в пути документа.
    """
    index = laid_out(tmp_path, {
        "Летучка/2026/п": head(date="2026-01-01"),
        "Курс/Python 2024/ш": head(date="2024-01-01"),
    })
    by_id = {r.id: r for r in index.all()}

    assert (by_id["п"].section, by_id["п"].subgroup) == ("Летучка", "2026")
    assert (by_id["ш"].section, by_id["ш"].subgroup) == ("Курс", "Python 2024")


def test_record_outside_any_directory_has_no_section(tmp_path: Path):
    """Запись легла мимо каталогов — она видна, просто без раздела.

    Пустой раздел — это сигнал «раскладку забыли», а не повод спрятать запись: спрятанное
    не ищут. Фильтр по разделу такую запись не покажет, но общий список обязан.
    """
    index = laid_out(tmp_path, {
        "Летучка/2026/п": head(date="2026-01-01"),
        "сирота": head(date="2026-05-01"),
    })
    by_id = {r.id: r for r in index.all()}

    assert len(index) == 2
    assert (by_id["сирота"].section, by_id["сирота"].subgroup) == ("", "")


def test_one_level_of_directories_gives_a_section_without_subsection(tmp_path: Path):
    """Каталог один — есть раздел, подраздела нет. Третий случай той же формулы."""
    index = laid_out(tmp_path, {"Летучка/а": head(date="2026-01-01")})
    record = index.all()[0]

    assert (record.section, record.subgroup) == ("Летучка", "")


def test_deeper_nesting_is_kept_not_dropped(tmp_path: Path):
    """Каталог глубже второго уровня склеивается в подраздел, а не теряется молча."""
    index = laid_out(tmp_path, {"Летучка/2026/октябрь/а": head(date="2026-10-01")})
    record = index.all()[0]

    assert (record.section, record.subgroup) == ("Летучка", "2026/октябрь")


def test_list_is_flat_and_sorted_by_date(tmp_path: Path):
    """Порядок по умолчанию — свежие сверху; при равной дате id сравнивается «по-человечески».

    ⚠️ Не украшение: у курса все лекции выложены одним днём, ничья разрывается по id, и по
    строке «…-10-» встаёт раньше «…-2-» — курс из 25 лекций читался вперемешку.
    """
    index = laid_out(tmp_path, {
        "Курс/МЛ/занятие-2": head(date="2025-01-01"),
        "Курс/МЛ/занятие-10": head(date="2025-01-01"),
        "Летучка/2026/свежая": head(date="2026-01-01"),
    })

    assert [r.id for r in index.all()] == ["свежая", "занятие-2", "занятие-10"]


def test_reading_direction_turns_the_whole_list_around(tmp_path: Path):
    """`order: asc` — учебный корпус целиком: его читают с первого занятия."""
    index = laid_out(tmp_path, {
        "Курс/МЛ/1": head(date="2025-01-01"),
        "Курс/МЛ/2": head(date="2025-02-01"),
    }, order="asc")

    assert [r.id for r in index.all()] == ["1", "2"]


def test_reading_direction_of_a_section_is_carried_to_the_front(tmp_path: Path):
    """Направление РАЗДЕЛА список не переставляет — он плоский; оно уезжает фронту.

    Отфильтровав список курсом, фронт обязан показать лекцию 1 первой. Вывести это из данных
    нельзя: даты у лекций одинаковые, а «читают подряд» — решение владельца, не свойство записей.
    """
    index = laid_out(tmp_path, {"Курс/МЛ/1": head(date="2025-01-01")},
                     section_order={"Курс": "asc"})

    assert index.section_order == {"Курс": "asc"}
