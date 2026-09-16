"""Шкала экрана из видео: детектор на синтетических кадрах, разбор ответов Vision, сборка
`slides.md`.

Детектор проверяется на нарисованной «записи»: слайды с текстом, полоса участников справа,
которая меняется сама по себе, билд (пункты появляются по одному), возврат к прежнему слайду и
живой экран. Каждое утверждение — грабли, оплаченные 12.09.2026 на семи живых записях:
без маски полосы 18 ложных разрезов из 70, dHash склеивал разные слайды одной вёрстки,
затишье в один кадр посреди перехода становилось «слайдом».

Зависимости детектора (numpy, scipy, Pillow) вне `app/requirements.txt` — без них тесты
пропускаются, а не падают: веб-морда переедет в отдельный репозиторий без них.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("PIL")

import slides_from_video as sv  # noqa: E402
from describe_slides import as_text, normalize_desc, parse_json, salvage_json, text_key  # noqa: E402
from make_slides_md import clean_visual, slide_line  # noqa: E402
from screen_refs import quote_time  # noqa: E402

W, H = 320, 180
STRIP = 280  # с этого столбца — «полоса участников»


def _slide(seed: int, lines: int = 4) -> np.ndarray:
    """«Слайд»: белый фон, тёмные блоки «текста и картинок» разной величины. Смена такого слайда
    перекрашивает 20-50 % кадра — как у настоящих (замерено: 20-90 %), а не единицы процентов."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W), 240, np.uint8)
    for k in range(lines):
        y = 15 + k * 32 + int(rng.integers(0, 6))
        x1 = 30 + int(rng.integers(60, 220))
        img[y:y + 22, 20:x1] = int(rng.integers(20, 90))
    return img


def _deck(frames_spec: list[tuple[np.ndarray, int]], seed: int = 1) -> np.ndarray:
    """Склеить «запись» из (кадр содержимого, сколько секунд держать) и дорисовать полосу
    участников, которая меняется сама по себе — как плитки конференции."""
    rng = np.random.default_rng(seed)
    out = []
    strip = rng.integers(0, 255, (H, W - STRIP)).astype(np.uint8)
    for img, secs in frames_spec:
        for _ in range(secs):
            f = img.copy()
            if rng.random() < 0.15:  # кто-то вошёл/вышел — одна из плиток перерисовалась
                y = int(rng.integers(0, H - 30))
                strip[y:y + 30] = rng.integers(0, 255, (30, W - STRIP)).astype(np.uint8)
            f[:, STRIP:] = strip
            out.append(f)
    return np.stack(out)


def _active(seed: int, secs: int) -> list[tuple[np.ndarray, int]]:
    """Живой экран: каждую секунду меняется небольшой кусок — строка набираемого кода.
    Именно небольшой: в настоящей IDE за секунду меняются курсор и одна строка, а не блок."""
    rng = np.random.default_rng(seed)
    base = _slide(seed, 6)
    spec = []
    for _ in range(secs):
        f = base.copy()
        y = int(rng.integers(20, 150))
        x = int(rng.integers(20, 120))
        f[y:y + 5, x:x + 80] = rng.integers(0, 255, (5, 80)).astype(np.uint8)
        spec.append((f, 1))
    return spec


@pytest.fixture(scope="module")
def deck():
    a, b, c = _slide(1), _slide(2), _slide(3)
    b_build = b.copy()
    b_build[150:170, 20:150] = 30  # появился ещё один пункт
    spec = [(a, 60), (b, 40), (b_build, 45), (c, 50)] + _active(9, 90) + [(a, 90)]
    frames = _deck(spec)
    p = sv.Params()
    mask, bbox = sv.layout_mask(frames, p)
    change = sv.activity(frames, mask, p)
    segs = sv.segment(frames, mask, change, p)
    slides = sv.slides_of(frames, segs, bbox, p)
    return {"frames": frames, "mask": mask, "bbox": bbox, "segs": segs, "slides": slides, "p": p}


def test_mask_excludes_participant_strip_but_not_slide(deck):
    """Полоса, которая перерисовывается сама по себе, — вне содержимого; область слайда — внутри."""
    mask = deck["mask"]
    assert mask[:, STRIP + 5:].mean() > 0.9
    assert mask[20:160, 30:200].mean() < 0.1


def test_strip_changes_do_not_cut_slides(deck):
    """Слайд A держится 40 с, и плитки за это время перерисовались не раз — сегмент один."""
    a_segments = [e for e in deck["slides"] if e["i0"] < 60]
    assert len(a_segments) == 1 and a_segments[0]["i0"] == 0 and a_segments[0]["i1"] == 60


def test_build_is_merged_into_one_slide(deck):
    """Пункт, появившийся на слайде B, — билд: один слайд, ключевой кадр из последнего состояния."""
    b = [e for e in deck["slides"] if e["i0"] <= 80 < e["i1"]]
    assert len(b) == 1
    assert b[0]["builds"] and b[0]["i1"] == 145 and b[0]["key"] >= 100
    assert not any(e["i0"] == 101 for e in deck["slides"])  # второе состояние не стало слайдом


def test_returning_slide_gets_same_identity(deck):
    """Слайд A показан в начале и в конце — один и тот же `slide`."""
    first, last = deck["slides"][0], deck["slides"][-1]
    assert first["slide"] == last["slide"]
    ids = {e["slide"] for e in deck["slides"]}
    assert len(ids) == 3  # A, B, C


def test_active_range_is_separate_kind(deck):
    """Набор кода — активный участок, а не череда секундных «слайдов»."""
    active = [s for s in deck["segs"] if s["kind"] == "active"]
    assert len(active) == 1
    assert 190 <= active[0]["i0"] <= 198 and active[0]["i1"] >= 280
    assert active[0]["motion"]["share"] > 0


def test_short_quiet_inside_transition_is_not_a_slide():
    """Затишье в один кадр посреди перехода не становится слайдом (ловилось: 60 слайдов
    вместо 52)."""
    change = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.5, 0, 0.5, 0.5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    act = sv.label_frames(change, sv.Params(min_slide=8))
    assert act[11:15].all()  # весь переход, включая тихий кадр 12, — активность
    assert not act[15:].any()


# --- разбор ответов Vision --------------------------------------------------------------------

def test_parse_json_accepts_raw_newlines_inside_strings():
    raw = '```json\n{"kind": "slide", "title": "T", "text": "строка 1\nстрока 2", "visual": "", "unreadable": false}\n```'
    out = parse_json(raw)
    assert out and out["text"] == "строка 1\nстрока 2"
    assert parse_json(None) is None and parse_json("нет объекта") is None


def test_salvage_truncated_answer():
    raw = '```json\n{"kind": "slide", "title": "conftest.py", "text": "import pytest\n@pytest.fixture\ndef order('
    out = salvage_json(raw)
    assert out and out["title"] == "conftest.py" and out["text"].startswith("import pytest")


def test_list_valued_text_becomes_lines():
    d = normalize_desc({"kind": "Slide", "title": ["Pytest"], "text": ["a", "b"], "visual": None})
    assert d["kind"] == "slide" and d["title"] == "Pytest" and d["text"] == "a\nb" and d["visual"] == ""
    assert as_text({"k": ["x"]}) == "k: x"


def test_text_key_ignores_spacing_and_case():
    a = {"title": "Что делать", "text": "Сервис,\nкоторый хочет"}
    b = {"title": "что  делать", "text": "сервис, который хочет"}
    assert text_key(a) == text_key(b)


# --- обращения и slides.md ----------------------------------------------------------------------

def test_quote_time_finds_first_word_of_quote():
    turn = {"words": [["Ну", 10.0, 10.2], ["и", 10.3, 10.4], ["здесь", 11.0, 11.3], ["вот", 11.4, 11.5],
                      ["нарисую", 11.6, 12.0], ["пирамиду.", 12.1, 12.8]]}
    assert quote_time(turn, "здесь вот нарисую") == 11.0
    assert quote_time(turn, "совсем другие слова тут") is None


def test_clean_visual_drops_decor_sentences():
    v = "Пирамида из трёх слоёв. Справа зелёный геометрический узор. Слева стрелка вниз."
    assert clean_visual(v) == "Пирамида из трёх слоёв. Слева стрелка вниз."


def test_slide_line_skips_people_and_keeps_transcript_shape():
    assert slide_line({"slide": 1, "desc": {"kind": "people", "title": "", "text": "", "visual": ""}}) is None
    line = slide_line({"slide": 7, "desc": {"kind": "slide", "title": "Архитектура", "text": "Kafka\n\nPostgres",
                                            "visual": "Схема: продюсер → брокер.", "unreadable": False}})
    assert line.startswith("Слайд 7 «Архитектура»:")
    assert "\n\n" not in line  # пустая строка разорвала бы реплику для чанкера
    assert "Изображено: Схема: продюсер → брокер." in line


def test_slides_md_lines_parse_as_transcript_turns(tmp_path):
    """Каждая строка `slides.md` — реплика вида `[Экран] <!-- t:… --> текст`: ровно то, что
    разбирает транскрипт-чанкер морага."""
    import re
    from make_slides_md import build
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "record.md").write_text('---\ntitle: "Доклад"\ndate: "2024-01-01"\nbranch: "Митапы"\n---\n\n[А] <!-- t:0.0 --> текст\n', encoding="utf-8")
    (rec / "record.slides.json").write_text(json.dumps({
        "summary": {"slides": 2},
        "slides": [{"n": 1, "slide": 1, "t0": 10.0, "t1": 40.0, "desc": {"kind": "slide", "title": "Один", "text": "a\nb", "visual": "", "unreadable": False}},
                   {"n": 2, "slide": 2, "t0": 40.0, "t1": 50.0, "desc": {"kind": "people", "title": "", "text": "", "visual": "", "unreadable": False}}],
        "samples": [{"n": 1, "t": 100.0, "desc": {"kind": "code", "title": "main.py", "text": "print(1)", "visual": "", "unreadable": False}}]},
        ensure_ascii=False), encoding="utf-8")
    (rec / "record.refs.json").write_text(json.dumps({"refs": [
        {"t": 12.5, "quote": "вот здесь", "kind": "region", "resolved": "блок Unit Tests"},
        {"t": 13.0, "quote": "а тут", "kind": "region", "resolved": "неясно"}]}, ensure_ascii=False), encoding="utf-8")
    text = build(rec)
    assert text.startswith('---\ntitle: "Доклад · экран"\n')
    assert 'record: "rec"' in text and "speakers" not in text
    turns = [p for p in text.split("\n---", 1)[1].split("\n\n") if p.strip()]
    pat = re.compile(r"^\[Экран\] <!-- t:\d+\.\d --> .+", re.S)
    assert len(turns) == 3 and all(pat.match(t) for t in turns)
    times = [float(re.search(r"t:([\d.]+)", t).group(1)) for t in turns]
    assert times == sorted(times) == [10.0, 12.5, 100.0]
