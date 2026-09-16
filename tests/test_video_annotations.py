"""`tools/make_annotations.py`: сайдкар аннотаций из шкалы экрана и обращений (ADR-0027)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import make_annotations as ma  # noqa: E402


def _slide(n, t0, t1, kind="slide", title="", text="", visual="", people=False, deck=None):
    """`n` — индекс показа, `deck` — номер слайда в колоде (возвращающийся слайд показан дважды)."""
    d = {"kind": kind, "title": title, "text": text, "visual": visual, "unreadable": False}
    e = {"n": n, "slide": deck if deck is not None else n, "t0": t0, "t1": t1, "frame": f"slides/s{n:03d}.jpg", "desc": d}
    if people:
        e["people"] = True
    return e


@pytest.fixture
def record(tmp_path):
    slides = [
        _slide(1, 84.0, 333.0, title="Очереди", text="Producer → Kafka → Consumer"),
        _slide(2, 333.0, 400.0, kind="app", title="", text="Окно Postgres " * 60),   # 840 знаков
        _slide(3, 400.0, 460.0, kind="diagram", title="Схема", visual="стрелка от продюсера к брокеру"),
        _slide(4, 460.0, 500.0, kind="people", people=True),
        _slide(5, 500.0, 560.0, title="Очереди", text="Producer → Kafka → Consumer", deck=1),  # слайд 1 показан снова
    ]
    segments = [{"kind": "active", "t0": 0.0, "t1": 84.0}, {"kind": "slide", "t0": 84.0, "t1": 500.0}]
    samples = [{"n": 1, "t": 30.0, "segment": 0, "desc": {"kind": "browser", "title": "Поиск", "text": "строка"}}]
    refs = [
        {"t": 140.5, "quote": "вот на этой схеме", "kind": "slide", "slide": 1, "resolved": "стрелка «ack»"},
        {"t": 350.0, "quote": "здесь видно", "kind": "region", "slide": 2, "resolved": "Неясно."},
        {"t": 470.0, "quote": "смотрите", "kind": "slide", "slide": 4, "resolved": "люди"},
        {"t": 480.0, "quote": "а вот тут", "kind": "slide", "slide": None},
        {"t": 520.0, "quote": "снова схема", "kind": "slide", "slide": 5, "resolved": "та же стрелка"},
    ]
    (tmp_path / "record.slides.json").write_text(json.dumps(
        {"version": "slides-v1", "slides": slides, "segments": segments, "samples": samples}), encoding="utf-8")
    (tmp_path / "record.refs.json").write_text(json.dumps({"refs": refs}), encoding="utf-8")
    return tmp_path


def test_boundaries_are_slide_and_segment_starts_without_zero(record):
    data = ma.build(record)
    assert [it["at"] for it in data["items"] if it["kind"] == "boundary"] == [84.0, 333.0, 400.0, 460.0, 500.0]


def test_screen_items_skip_people_and_cut_app_windows(record):
    screens = [it for it in ma.build(record)["items"] if it["kind"] == "screen"]
    subs = [it["sub"] for it in screens]
    assert "people" not in subs and subs == ["browser", "slide", "app", "diagram", "slide"]
    app = next(it for it in screens if it["sub"] == "app")
    assert 300 <= len(app["text"]) <= 305 and app["text"].endswith("…")   # --app-text short: 300 знаков + « …»
    slide = next(it for it in screens if it["sub"] == "slide")
    assert slide["title"] == "Очереди" and slide["slide"] == 1 and slide["t0"] == 84.0 and slide["t1"] == 333.0
    assert slide["label"] == "Слайд 1" and app["label"] == "Окно программы"   # подпись строки в выдаче — от корпуса
    assert slide["frame"] == "slides/s001.jpg"                                # ссылка на кадр — для показа скрина у момента
    again = [it for it in screens if it["sub"] == "slide"][1]
    assert again["slide"] == 1 and again["t0"] == 500.0                    # повторный показ — свой отрезок
    diagram = next(it for it in screens if it["sub"] == "diagram")
    assert diagram["text"].startswith("Изображено:")
    sample = next(it for it in screens if it["sub"] == "browser")
    assert sample["t0"] == sample["t1"] == 30.0


def test_refs_become_pins_with_resolution(record):
    refs = [it for it in ma.build(record)["items"] if it["kind"] == "ref"]
    # люди и без слайда — пропуск; `slide` обращения — индекс ПОКАЗА: повторный показ слайда 1 → 500.0, не 84.0
    assert [(r["at"], r["to"]) for r in refs] == [(140.5, 84.0), (350.0, 333.0), (520.0, 500.0)]
    assert refs[0]["text"] == "стрелка «ack»" and refs[0]["quote"] == "вот на этой схеме"
    assert "text" not in refs[1]                          # «неясно» — только привязка


def test_items_sorted_by_time(record):
    times = [it.get("at", it.get("t0")) for it in ma.build(record)["items"]]
    assert times == sorted(times)


def test_write_is_idempotent_for_mtime(record):
    data = ma.build(record)
    assert ma.write(record, data) is True
    out = record / "record.annotations.json"
    os.utime(out, (1_700_000_000, 1_700_000_000))
    assert ma.write(record, data) is False
    assert out.stat().st_mtime == 1_700_000_000           # холостая перезапись = переиндексация; её нет
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == "annotations-v1"


def test_no_slides_sidecar_gives_none(tmp_path):
    assert ma.build(tmp_path) is None
