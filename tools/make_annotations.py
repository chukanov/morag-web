#!/usr/bin/env python3
"""`record.slides.json` + `record.refs.json` → `record.annotations.json` — сайдкар аннотаций для
движка (ADR-0027 в мораге). Три рода элементов:

  boundary  секунда смены слайда / участка экрана — подсказка границы чанкеру;
  screen    что было на экране на отрезке `t0..t1` (заголовок, текст, подрод) — поле чанка (этап D);
  ref       привязка: слова докладчика в `at` относятся к экрану, начавшемуся в `to`, плюс
            разрешение по кадру («вот здесь» → что именно там было).

Политика текста та же, что у `make_slides_md.py`: авторские слайды (slide/code/diagram) дословно до
`--max-text`, окна программ (app/browser/terminal) — по `--app-text` (по умолчанию 300 знаков:
чужие данные в окне Jira не должны становиться искомыми, принцип 5). Кадры с людьми не попадают
никуда. Пишется ТОЛЬКО при изменении содержимого: mtime сайдкара входит в `updated_at` документа,
и холостая перезапись стоила бы переиндексации записи.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_slides_md import AUTHORED, clean_visual, cut, one_paragraph  # noqa: E402

VERSION = "annotations-v1"
UNRESOLVED = re.compile(r"^\W*(неясно|люди)", re.I)


def screen_item(e: dict, t0: float, t1: float, app_text: str = "short", max_text: int = 2500) -> dict | None:
    """Элемент `screen` из записи слайда/выборки; None — людей, нечитаемое и пустое не берём."""
    d = e.get("desc")
    if not d or d.get("kind") == "people" or d.get("unreadable") or e.get("people"):
        return None
    title = (d.get("title") or "").strip()
    text = one_paragraph(d.get("text", ""))
    if d["kind"] in AUTHORED:
        text = cut(text, max_text)
    else:
        text = cut(text, {"none": 0, "short": 300, "full": max_text}[app_text])
    visual = clean_visual(d.get("visual", ""))
    if not (title or text or visual):
        return None
    if text.replace("\n", " ").strip() == title:
        text = ""
    if visual:
        text = (text + "\n" if text else "") + f"Изображено: {visual}"
    what = {"slide": "Слайд", "code": "Код на экране", "app": "Окно программы", "terminal": "Терминал",
            "browser": "Браузер", "diagram": "Схема", "other": "Экран"}.get(d["kind"], "Экран")
    item = {"kind": "screen", "t0": round(t0, 1), "t1": round(t1, 1), "sub": d["kind"], "label": what}
    if "slide" in e and d["kind"] == "slide":
        item["slide"] = e["slide"]  # номер слайда в колоде — для подписи «Слайд N», как в slides.md
        item["label"] = f"Слайд {e['slide']}"
    if title:
        item["title"] = title
    if text:
        item["text"] = text
    if e.get("frame"):
        # кадр, по которому Vision это описал (`slides/sNNN.jpg` в каталоге записи; кадры с людьми
        # удалены и ссылки не получают) — для показа скрина у момента на сайте (владелец, 13.09)
        item["frame"] = e["frame"]
    return item


def build(record: Path, app_text: str = "short", max_text: int = 2500) -> dict | None:
    slides_path = record / "record.slides.json"
    if not slides_path.is_file():
        return None
    sl = json.loads(slides_path.read_text(encoding="utf-8"))
    refs_path = record / "record.refs.json"
    refs = json.loads(refs_path.read_text(encoding="utf-8"))["refs"] if refs_path.is_file() else []
    slides = sl.get("slides", [])
    # ⚠️ `slide` у обращения — индекс ПОКАЗА `n` (кадр `slides/s005.jpg`), а не номер слайда в колоде
    # `slide`: возвращающийся слайд показывается несколько раз, и по номеру колоды привязка уехала бы
    # на более поздний показ (ловилось: 71 из 104 привязок указывали в будущее).
    by_n = {s["n"]: s for s in slides}

    items: list[dict] = []
    # границы: старт каждого слайда и каждого участка (демо тоже смена темы); начало записи — не граница
    starts = {round(s["t0"], 1) for s in slides} | {round(seg["t0"], 1) for seg in sl.get("segments", [])}
    items += [{"kind": "boundary", "at": t} for t in sorted(starts) if t > 0]
    for s in slides:
        it = screen_item(s, s["t0"], s["t1"], app_text, max_text)
        if it:
            items.append(it)
    for s in sl.get("samples", []):
        it = screen_item(s, s["t"], s["t"], app_text, max_text)  # точка: кадр выборки из активного участка
        if it:
            items.append(it)
    for r in refs:
        slide = by_n.get(r.get("slide"))
        if slide is None or slide.get("people") or (slide.get("desc") or {}).get("kind") == "people":
            continue  # референт не найден или это люди — ни привязки, ни текста
        it = {"kind": "ref", "at": round(r["t"], 2), "to": round(slide["t0"], 1),
              "sub": r.get("kind", "slide"), "quote": r["quote"]}
        res = (r.get("resolved") or "").strip()
        if res and not UNRESOLVED.match(res):
            it["text"] = res
        items.append(it)
    items.sort(key=lambda it: (it.get("at", it.get("t0", 0.0)), it["kind"]))
    return {"version": VERSION, "record": record.name, "source": "record.slides.json + record.refs.json",
            "items": items}


def write(record: Path, data: dict) -> bool:
    """True — файл записан (содержимое изменилось); False — совпало, mtime не тронут."""
    out = record / "record.annotations.json"
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if out.is_file() and out.read_text(encoding="utf-8") == text:
        return False
    out.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("records", nargs="+", type=Path, help="каталоги записей с record.slides.json")
    ap.add_argument("--app-text", choices=("none", "short", "full"), default="short")
    ap.add_argument("--max-text", type=int, default=2500)
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args()
    for rec in a.records:
        data = build(rec, a.app_text, a.max_text)
        if data is None:
            print(f"{rec.name}: нет record.slides.json — пропуск", file=sys.stderr)
            continue
        if a.stdout:
            print(json.dumps(data, ensure_ascii=False, indent=1))
            continue
        kinds = {}
        for it in data["items"]:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
        changed = write(rec, data)
        print(f"{rec.name}: {'записан' if changed else 'без изменений'} — "
              + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
