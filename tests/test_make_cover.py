"""make_cover.py: обложка выбирается детерминированно и по правилу — титульный по заголовку,
иначе первый ранний слайд, иначе самый долгий, иначе кадр без людей; рука владельца не трогается;
запись идёт только при изменении."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_cover as mc  # noqa: E402


def show(n, t0, t1, kind="slide", title="", frame=True, people=False):
    return {"n": n, "t0": t0, "t1": t1, "desc": {"kind": kind, "title": title}, "people": people,
            **({"frame": f"slides/s{n:03d}.jpg"} if frame else {})}


def test_title_slide_wins_by_similarity():
    slides = {"source": {"duration": 3000}, "slides": [
        show(1, 0, 20, "people", "", people=True),
        show(2, 20, 40, "slide", "Немного о команде"),
        show(3, 40, 90, "slide", "Kafka без боли: как мы пережили миграцию"),
    ]}
    c = mc.choose_cover(slides, "Kafka без боли: как мы пережили миграцию")
    assert c["frame"] == "slides/s003.jpg" and c["n"] == 3 and "совпал" in c["why"]


def test_intro_splash_wins_when_it_carries_the_title():
    """Заставка в начале видео (`intro`): название в её тексте — обложка она, а не слайд."""
    slides = {"source": {"duration": 3000},
              "intro": {"t": 2.0, "frame": "slides/intro.jpg",
                        "desc": {"kind": "other", "title": "", "text": "Kafka без боли: как мы пережили миграцию · Кузнецова"}},
              "slides": [show(1, 30, 90, "slide", "План"), show(2, 90, 400, "slide", "Немного о команде")]}
    c = mc.choose_cover(slides, "Kafka без боли: как мы пережили миграцию")
    assert c["frame"] == "slides/intro.jpg" and c["n"] == 0 and "заставка" in c["why"]


def test_authored_splash_wins_on_a_partial_match_but_a_window_does_not():
    """Первый кадр — почти всегда титульная карточка, ей хватает частичного совпадения (0.3).
    Замерено на 127 заставках: ниже порога остаются служебные карточки записи собрания."""
    base = {"source": {"duration": 3000}, "slides": [show(1, 30, 90, "slide", "План")]}
    card = {**base, "intro": {"t": 0.0, "frame": "slides/intro.jpg",
                              "desc": {"kind": "slide", "title": "Очереди", "text": ""}}}
    assert mc.choose_cover(card, "Очереди Kafka понятно")["n"] == 0
    window = {**base, "intro": {"t": 0.0, "frame": "slides/intro.jpg",
                                "desc": {"kind": "browser", "title": "Очереди", "text": ""}}}
    assert mc.choose_cover(window, "Очереди Kafka понятно")["n"] == 1
    # служебная карточка записи собрания: с названием доклада не пересекается вовсе
    system = {**base, "intro": {"t": 0.0, "frame": "slides/intro.jpg",
                                "desc": {"kind": "slide", "title": "Новая встреча в канале",
                                         "text": "2023-11-14 09:58 UTC"}}}
    assert mc.choose_cover(system, "Занятие 7. Компоненты и сервисы")["n"] == 1


def test_intro_with_people_or_without_title_match_does_not_beat_slides():
    base = {"source": {"duration": 3000}, "slides": [show(1, 30, 90, "slide", "План")]}
    people = {**base, "intro": {"t": 1.0, "people": True, "desc": {"kind": "people", "title": "", "text": ""}}}
    assert mc.choose_cover(people, "Другое")["n"] == 1
    generic = {**base, "intro": {"t": 1.0, "frame": "slides/intro.jpg", "desc": {"kind": "other", "title": "Митап", "text": ""}}}
    assert mc.choose_cover(generic, "Другое название доклада")["n"] == 1
    # слайдов нет вовсе — заставка годится, только если это авторский кадр с текстом: фото сцены
    # с экраном Vision тоже зовёт «slide», но без единого слова (ловилось на концерте)
    alone = {"source": {"duration": 100}, "slides": [],
             "intro": {"t": 1.0, "frame": "slides/intro.jpg", "desc": {"kind": "slide", "title": "Митап", "text": ""}}}
    assert mc.choose_cover(alone, "Другое")["n"] == 0
    photo = {"source": {"duration": 100}, "slides": [],
             "intro": {"t": 1.0, "frame": "slides/intro.jpg", "desc": {"kind": "slide", "title": "", "text": ""}}}
    assert mc.choose_cover(photo, "Другое") is None
    other = {"source": {"duration": 100}, "slides": [], "intro": generic["intro"]}
    assert mc.choose_cover(other, "Другое") is None


def test_speaker_on_stage_is_a_cover_but_title_slide_still_wins():
    """Докладчик на сцене (владелец, 14.09: «принято вставлять фотографии докладчика») — обложка,
    когда титульного слайда нет; титульный по-прежнему сильнее."""
    stage = {"t": 20.0, "frame": "slides/intro.jpg", "people": True, "who": "speaker", "setting": "stage",
             "desc": {"kind": "people", "title": "", "text": ""}}
    slides = {"source": {"duration": 3000}, "intro": stage,
              "slides": [show(1, 30, 90, "slide", "План"), show(2, 90, 400, "slide", "Немного о команде")]}
    c = mc.choose_cover(slides, "Совсем другое название")
    assert c["frame"] == "slides/intro.jpg" and "докладчик" in c["why"]
    titled = {**slides, "slides": [*slides["slides"], show(3, 400, 500, "slide", "Совсем другое название")]}
    assert mc.choose_cover(titled, "Совсем другое название")["n"] == 3


def test_speaker_in_webcam_is_a_cover_only_without_slides_and_audience_never():
    webcam = {"t": 0.0, "frame": "slides/intro.jpg", "people": True, "who": "speaker", "setting": "webcam",
              "desc": {"kind": "people", "title": "", "text": ""}}
    with_slides = {"source": {"duration": 3000}, "intro": webcam, "slides": [show(1, 30, 90, "slide", "План")]}
    assert mc.choose_cover(with_slides, "Другое")["n"] == 1, "при слайдах лицо в камере не обложка — список стал бы стеной лиц"
    alone = {"source": {"duration": 100}, "intro": webcam, "slides": []}
    assert mc.choose_cover(alone, "Другое")["n"] == 0
    audience = {"source": {"duration": 100}, "slides": [],
                "intro": {**webcam, "who": "audience", "setting": "stage"}}
    assert mc.choose_cover(audience, "Другое") is None
    # кадр шкалы с докладчиком на сцене — тоже обложка, если титульного нет
    stage_show = {**show(2, 100, 200, "people"), "people": True, "who": "speaker", "setting": "stage"}
    mixed = {"source": {"duration": 3000}, "slides": [show(1, 30, 90, "slide", "План"), stage_show]}
    assert mc.choose_cover(mixed, "Другое")["n"] == 2


def test_first_early_long_slide_when_no_title_match():
    slides = {"source": {"duration": 1000}, "slides": [
        show(1, 0, 5, "slide", "Пауза"),                   # короче 10 с — мимо
        show(2, 5, 60, "slide", "План"),
        show(3, 800, 900, "slide", "Итоги"),                # не в первой четверти
    ]}
    c = mc.choose_cover(slides, "Совсем другое название доклада")
    assert c["n"] == 2 and "начале" in c["why"]


def test_longest_authored_then_any_frame_without_people():
    slides = {"source": {"duration": 1000}, "slides": [
        show(1, 900, 905, "slide", "Q&A"),
        show(2, 905, 990, "diagram", "Схема"),
    ]}
    assert mc.choose_cover(slides, "Доклад")["n"] == 2
    demo = {"source": {"duration": 600}, "slides": [
        show(1, 0, 30, "people", "", people=True),
        show(2, 30, 90, "browser", "Jira"),
        show(3, 90, 400, "app", "IDE"),
    ]}
    c = mc.choose_cover(demo, "Демо")
    assert c["n"] == 2 and "без чужих лиц" in c["why"]
    assert mc.choose_cover({"slides": [show(1, 0, 30, people=True)]}, "x") is None
    assert mc.choose_cover({"slides": [show(1, 0, 30, frame=False)]}, "x") is None


def test_crop_box_cuts_participants_strip_and_speaker_label():
    # полоса участников справа на 12 % ширины → режем 14 % (запас 2 %); подпись снизу — 6 % высоты
    assert mc.crop_box(1000, 500, {"participants": "right", "share": 0.12, "label": "bottom-left"}) == (0, 0, 860, 460)
    assert mc.crop_box(1000, 500, {"participants": "bottom", "share": 0.2, "label": "none"}) == (0, 0, 1000, 390)
    assert mc.crop_box(1000, 500, {"participants": "left", "share": 0.1, "label": "top-right"}) == (120, 30, 1000, 500)
    assert mc.crop_box(1000, 500, {"participants": "none", "share": 0.0, "label": "none"}) == (0, 0, 1000, 500)
    assert mc.crop_box(1000, 500, {"participants": "right", "share": 0.9}) == (0, 0, 580, 500)   # доля ограничена 0.4


def test_rendered_cover_keeps_its_source_and_is_not_redone(tmp_path: Path):
    rec = tmp_path / "2026-01-01-kafka"
    rec.mkdir()
    (rec / "record.md").write_text('---\ntitle: "Kafka без боли"\ndate: "2026-01-01"\n---\n', encoding="utf-8")
    data = {"source": {"duration": 100}, "slides": [show(1, 0, 50, "slide", "Kafka без боли")],
            "cover": {"frame": "slides/cover.jpg", "source": "slides/s001.jpg", "n": 1, "why": "x", "crop": {}}}
    (rec / "slides").mkdir()
    (rec / "slides" / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (rec / "record.slides.json").write_text(json.dumps(data), encoding="utf-8")
    assert mc.process(rec, do_render=True).startswith("без изменений: slides/cover.jpg")
    assert mc.process(rec).startswith("без изменений: slides/cover.jpg")     # без --render тоже не трогаем


def test_process_writes_only_on_change_and_respects_owner(tmp_path: Path):
    rec = tmp_path / "2026-01-01-kafka"
    rec.mkdir()
    (rec / "record.md").write_text('---\ntitle: "Kafka без боли"\ndate: "2026-01-01"\n---\n', encoding="utf-8")
    data = {"source": {"duration": 100}, "slides": [show(1, 0, 50, "slide", "Kafka без боли"), show(2, 50, 100, "slide", "Итоги")]}
    p = rec / "record.slides.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert mc.process(rec).startswith("slides/s001.jpg")
    m1 = p.stat().st_mtime_ns
    assert mc.process(rec).startswith("без изменений")
    assert p.stat().st_mtime_ns == m1                       # холостая запись = переиндексация, её нет
    data = json.loads(p.read_text(encoding="utf-8"))
    data["cover"] = {"frame": "slides/s002.jpg", "n": 2, "by": "owner"}
    p.write_text(json.dumps(data), encoding="utf-8")
    assert "владельца" in mc.process(rec)
    assert json.loads(p.read_text(encoding="utf-8"))["cover"]["n"] == 2
