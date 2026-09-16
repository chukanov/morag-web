#!/usr/bin/env python3
"""Обложка записи — кадр слайда из `record.slides.json` (владелец, 13.09: «выбор среди слайдов
титульного или того, который лучше подойдёт картинкой-заголовком карточки»).

Выбор детерминирован, без модели: у каждого показа есть заголовок, род, кадр, длительность и
пометка «люди». Кандидаты — показы с кадром и без людей, авторских родов (слайд, схема, код);
окна программ и браузер — только если ничего другого нет. Правило:
  1. слайд, чей заголовок лучше всего совпадает с названием записи (обычно это титульный);
  2. иначе первый авторский слайд, показанный ≥ 10 с в первой четверти доклада;
  3. иначе самый долгий авторский слайд;
  4. иначе первый кадр без людей; нет и его — обложки нет (карточка без картинки).

⚠️ Кадр ≠ обложка (принцип 5). Замечено 13.09 на живой карточке: в кадре осталась полоса
миниатюр участников видеозвонка — лица и фамилии коллег справа, плюс подпись с именем говорящего
поверх слайда; детектор полосу не отрезал (она меняется редко, маска её не ловит). Поэтому
обложка — ОТДЕЛЬНЫЙ файл `slides/cover.jpg` (`--render`): Vision смотрит на выбранный кадр и
говорит, где полоса участников и подпись, кадр обрезается по их краю и уменьшается до 640 px.
Без `--render` (или если Vision недоступен) `cover.frame` указывает на сырой кадр — на карточку
такое лучше не выпускать.

Результат — поле `cover` в `record.slides.json`: `{"frame": что показывать, "source": исходный
кадр, "n", "why", "crop": …, "checked": …}`. Рука владельца — `cover` с `"by": "owner"` — не
трогается. ⚠️ Пишет только при изменении содержимого.

  python3 tools/make_cover.py <каталог записи> [--dry]      # только выбор
  $PY tools/make_cover.py --all --render                    # выбор + проверка Vision + cover.jpg
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_slides_md import read_header  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
AUTHORED = {"slide", "diagram", "code"}
INTRO_SIM = 0.3   # порог совпадения с названием для авторской заставки (у слайда — 0.5)
MIN_SHOW_SEC = 10.0
EARLY_SHARE = 0.25
COVER_NAME = "slides/cover.jpg"
COVER_WIDTH = 640
STOP = {"пятничное", "часть", "доклад", "лекция", "занятие", "шифт", "the", "and", "для", "или", "как", "про", "что", "это"}

CHECK_PROMPT = """На кадре — экран записи встречи (слайд или окно программы). Ответь ТОЛЬКО JSON вида
{"participants": "none|right|left|bottom|top", "share": 0.0, "label": "none|bottom-left|bottom-right|top-left|top-right"}
participants — с какой стороны кадра стоит полоса миниатюр участников видеозвонка (маленькие видео с лицами и именами); share — какую долю ширины (для right/left) или высоты (для top/bottom) она занимает, число от 0 до 0.4; label — где поверх кадра стоит подпись с именем говорящего (маленькая плашка с текстом), none — если нет."""


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w-]+", (text or "").lower()) if len(w) >= 3 and w not in STOP}


def similarity(a: str, b: str) -> float:
    """Доля слов названия записи, найденных в заголовке слайда (0..1)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


def usable(s: dict) -> bool:
    """Кадр годится на обложку: есть на диске и без чужих лиц. Докладчик — не чужое лицо
    (владелец, 14.09: «обычно принято вставлять фотографии докладчика»), `who` ставит второй
    вопрос Vision в describe_slides."""
    return bool(s.get("frame")) and (not s.get("people") or s.get("who") == "speaker")


def choose_cover(slides: dict, title: str) -> dict | None:
    shows = [s for s in slides.get("slides") or [] if usable(s)]
    # Заставка — первый видимый кадр видео (`intro_frame.py`, владелец 14.09): часто это и есть
    # титульный кадр выступления, а в шкалу он не попадает — короче слайда. Кандидат наравне со
    # слайдами по совпадению с названием; название на заставке бывает и в тексте, не только в
    # заголовке — берём лучшее из двух.
    intro = slides.get("intro") or {}
    intro_ok = usable(intro) and not (intro.get("desc") or {}).get("unreadable")
    speaker_intro = intro_ok and intro.get("people") and intro.get("who") == "speaker"
    if not shows and not intro_ok:
        return None
    if intro_ok and not speaker_intro:
        d = intro.get("desc") or {}
        sim = max(similarity(title, d.get("title") or ""), similarity(title, d.get("text") or ""))
        # У АВТОРСКОГО первого кадра порог ниже, чем у слайда из середины (0.3 против 0.5):
        # первый кадр видео — почти всегда титульная карточка доклада, и совпадения хватает
        # частичного («Ретрозагрузки» при названии «Доклад. Ретрозагрузки просто…»).
        # Замерено 14.09 по 127 заставкам: ниже 0.3 остаются только служебные карточки записи
        # («Новое собрание в канале», 0.17-0.25), а на 0.33 стоят три настоящих титульных.
        # Окно браузера или программы на первом кадре — не заставка, ему прежний порог.
        if sim >= 0.5 or (sim >= INTRO_SIM and d.get("kind") in AUTHORED):
            return {"frame": intro["frame"], "n": 0, "why": f"заставка в начале видео: совпала с названием на {sim:.0%}"}
        # Слайдов нет вовсе — заставка годится, только если это авторский кадр С ТЕКСТОМ: фото
        # сцены с экраном Vision тоже зовёт «slide» (ловилось на записи концерта — на обложку
        # попали бы люди), а у настоящей заставки есть хоть слово.
        if not shows and d.get("kind") in AUTHORED and (d.get("title") or d.get("text") or "").strip():
            return {"frame": intro["frame"], "n": 0, "why": "заставка в начале видео — слайдов нет"}
    if not shows:
        # слайдов нет вовсе — докладчик в кадре (и в окне камеры тоже) лучше пустой плашки
        if speaker_intro:
            return {"frame": intro["frame"], "n": 0, "why": f"докладчик в кадре ({intro.get('setting') or 'other'}) — слайдов нет"}
        return None
    duration = float((slides.get("source") or {}).get("duration") or 0) or max(float(s.get("t1") or 0) for s in shows)
    authored = [s for s in shows if (s.get("desc") or {}).get("kind") in AUTHORED]
    pool = authored or shows

    def show_len(s: dict) -> float:
        return float(s.get("t1") or 0) - float(s.get("t0") or 0)

    # 1. заголовок слайда ~ название записи
    scored = [(similarity(title, (s.get("desc") or {}).get("title") or ""), -float(s.get("t0") or 0), s) for s in pool]
    best = max(scored, key=lambda x: (x[0], x[1]))
    if best[0] >= 0.5:
        return {"frame": best[2]["frame"], "n": best[2]["n"], "why": f"заголовок слайда совпал с названием на {best[0]:.0%}"}
    # 1а. Докладчик НА СЦЕНЕ — принятая обложка выступления: выше первого слайда, ниже титульного
    # (титульный носит название). Заставка — раньше кадров шкалы. В окне камеры при слайдах —
    # нет: иначе список стал бы стеной лиц вместо слайдов.
    if speaker_intro and intro.get("setting") == "stage":
        return {"frame": intro["frame"], "n": 0, "why": "докладчик на сцене в начале видео"}
    for s in sorted(shows, key=lambda s: float(s.get("t0") or 0)):
        if s.get("people") and s.get("who") == "speaker" and s.get("setting") == "stage":
            return {"frame": s["frame"], "n": s["n"], "why": "докладчик на сцене"}
    # 2. первый авторский слайд, показанный ≥ 10 с в первой четверти
    for s in sorted(authored, key=lambda s: float(s.get("t0") or 0)):
        if show_len(s) >= MIN_SHOW_SEC and float(s.get("t0") or 0) <= duration * EARLY_SHARE:
            return {"frame": s["frame"], "n": s["n"], "why": f"первый слайд в начале доклада, показан {show_len(s):.0f} с"}
    # 3. самый долгий авторский
    if authored:
        s = max(authored, key=show_len)
        return {"frame": s["frame"], "n": s["n"], "why": f"самый долгий слайд, {show_len(s):.0f} с"}
    # 4. первый годный кадр (окно программы, браузер, докладчик в камере)
    s = min(shows, key=lambda s: float(s.get("t0") or 0))
    return {"frame": s["frame"], "n": s["n"], "why": "слайдов нет — первый кадр экрана без чужих лиц"}


def crop_box(width: int, height: int, check: dict) -> tuple[int, int, int, int]:
    """Рамка обложки по ответу Vision: полоса участников и подпись отрезаются с запасом 2 %."""
    left, top, right, bottom = 0, 0, width, height
    # Vision иногда отвечает углом («bottom-right»: одна миниатюра в углу) — берём сторону
    side = str(check.get("participants") or "none").split("-")[0]
    share = min(0.4, max(0.0, float(check.get("share") or 0)))
    if side != "none" and share > 0:
        cut = share + 0.02
        if side == "right":
            right = int(width * (1 - cut))
        elif side == "left":
            left = int(width * cut)
        elif side == "bottom":
            bottom = int(height * (1 - cut))
        elif side == "top":
            top = int(height * cut)
    label = str(check.get("label") or "none")
    # 8 %, а не 6: при 6 % от подписи «Экран …» оставался обрезанный край (ловилось 14.09)
    if label.startswith("bottom"):
        bottom = min(bottom, int(height * 0.92))
    elif label.startswith("top"):
        top = max(top, int(height * 0.06))
    return left, top, right, bottom


def render(rec: Path, source: str, check: dict) -> dict:
    """`slides/cover.jpg` из исходного кадра по рамке; возвращает `crop` для сайдкара."""
    from PIL import Image  # noqa: PLC0415 — только в video-venv
    img = Image.open(rec / source).convert("RGB")
    box = crop_box(img.width, img.height, check)
    out = img.crop(box)
    if out.width > COVER_WIDTH:
        out = out.resize((COVER_WIDTH, round(out.height * COVER_WIDTH / out.width)), Image.LANCZOS)
    (rec / COVER_NAME).parent.mkdir(exist_ok=True)
    out.save(rec / COVER_NAME, "JPEG", quality=85, optimize=True)
    return {"box": [round(box[0] / img.width, 3), round(box[1] / img.height, 3),
                    round(box[2] / img.width, 3), round(box[3] / img.height, 3)], "size": [out.width, out.height]}


async def check_frame(rec: Path, source: str) -> dict | None:
    """Vision: где полоса участников и подпись говорящего на выбранном кадре."""
    import httpx  # noqa: PLC0415
    from describe_slides import MODEL, describe, load_env  # noqa: PLC0415
    env = load_env()
    async with httpx.AsyncClient(timeout=120) as client:
        out, meta = await describe(client, env, rec / source, MODEL, CHECK_PROMPT, asyncio.Semaphore(1))
    if not isinstance(out, dict):
        print(f"    ⚠️ Vision: {meta.get('error') or 'нет ответа'}")
        return None
    return {"participants": str(out.get("participants") or "none"), "share": float(out.get("share") or 0),
            "label": str(out.get("label") or "none"), "model": MODEL,
            "at": _dt.datetime.now().isoformat(timespec="seconds")}


def process(rec: Path, dry: bool = False, do_render: bool = False, redo: bool = False) -> str:
    path = rec / "record.slides.json"
    if not path.is_file():
        return "нет шкалы"
    data = json.loads(path.read_text(encoding="utf-8"))
    old = data.get("cover") or {}
    if old.get("by") == "owner":
        return "рука владельца — не трогаю"
    title = read_header(rec / "record.md").get("title", "").strip().strip('"') if (rec / "record.md").is_file() else ""
    choice = choose_cover(data, title)
    if choice is None:
        if "cover" in data:
            del data["cover"]
            if not dry:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            return "обложки нет (снята)"
        return "обложки нет"
    same_source = (old.get("source") or old.get("frame")) == choice["frame"]
    rendered = old.get("source") and (rec / COVER_NAME).is_file()
    if same_source and (rendered or not do_render) and not (redo and do_render):
        return f"без изменений: {old.get('frame')}"
    cover = dict(choice)
    if do_render and not dry:
        check = asyncio.run(check_frame(rec, choice["frame"]))
        if check is None:
            return f"выбран {choice['frame']}, но Vision не ответил — cover.jpg не собран"
        crop = render(rec, choice["frame"], check)
        cover = {"frame": COVER_NAME, "source": choice["frame"], "n": choice["n"], "why": choice["why"],
                 "checked": check, "crop": crop}
    if data.get("cover") == cover:
        return f"без изменений: {cover['frame']}"
    data["cover"] = cover
    if not dry:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tail = ""
    if cover.get("checked"):
        c = cover["checked"]
        tail = f" · участники: {c['participants']} {c['share']:.2f}, подпись: {c['label']}"
    return f"{cover['frame']} ← {choice['frame']} — {choice['why']}{tail}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", nargs="?", type=Path)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--root", type=Path, default=None, help="каталог записей (по умолчанию — каталог записей первого пространства корпуса)")
    ap.add_argument("--render", action="store_true", help="проверить кадр через Vision и собрать slides/cover.jpg (нужен video-venv)")
    ap.add_argument("--redo", action="store_true", help="с --render: пересобрать cover.jpg, даже если он уже есть")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.root is None:
        import spaces  # noqa: PLC0415
        a.root = spaces.records_dirs()[0]
    recs = sorted(p.parent for p in a.root.glob("**/record.slides.json")) if a.all else [a.record]
    if not recs or recs == [None]:
        ap.error("укажите каталог записи или --all")
    changed = 0
    for rec in recs:
        res = process(rec, a.dry, a.render, a.redo)
        changed += not res.startswith(("без изменений", "нет шкалы", "рука", "обложки нет"))
        print(f"  {rec.name[:52]:52s} {res}")
    print(f"записей {len(recs)}, обложек записано {changed}{' (dry)' if a.dry else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
