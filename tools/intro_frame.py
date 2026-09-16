#!/usr/bin/env python3
"""Первый ВИДИМЫЙ кадр видео — кандидат на обложку (`intro` в record.slides.json).

Владелец (14.09): первыми кадрами видео часто идёт ЗАСТАВКА выступления — название, докладчик,
логотип встречи. Детектор шкалы её теряет: заставка короче `min_slide` (8 с) или лежит внутри
плавного появления, и в `slides[]` не попадает — а значит не доходит и до выбора титульного в
`make_cover.py`. Отсюда отдельный шаг: читаем первые `INTRO_SEC` секунд серым потоком 1 к/с тем
же путём, что детектор (локально или по ssh), берём первый кадр, который видно — не тёмный, с
содержимым и уже неподвижный (не середина появления), — режем jpeg по той же рамке, что у
остальных кадров записи, и пишем `intro`. Дальше как у слайдов: `describe_slides.py` описывает
его (люди → кадр удаляется), `make_cover.py` считает его кандидатом наравне со слайдами.

ⓘ Если первый слайд шкалы сам начинается в первые секунды — заставка уже в кандидатах, дубль
не пишется. Шкала (`slides[]`, номера показов, привязки обращений) не меняется вовсе.

Запуск:
  $PY tools/intro_frame.py <видео> --record <каталог записи>
  $PY tools/intro_frame.py '<путь на сервере>' --remote user@host --record <каталог записи>
  $PY tools/intro_frame.py --all --remote user@host        # весь корпус: видео по `media:` шапки, как у батча
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from slides_from_video import Params, Source, as_source, save_frame  # noqa: E402

INTRO_SEC = 30
FRAME = "slides/intro.jpg"
DARK = 28.0    # средняя яркость ниже — чёрный экран или затемнение
FLAT = 10.0    # разброс яркости ниже — ровная заливка без содержимого
SAME = 0.90    # корреляция выше — соседний кадр это ТА ЖЕ картинка, а не другая сцена
RISE = 3.0     # …и она ещё разгорается: средняя яркость подросла хотя бы на столько


def head_frames(video: "Path | str | Source", p: Params, seconds: int = INTRO_SEC) -> np.ndarray:
    """Серые кадры (N, H, W) первых `seconds` секунд, 1 к/с — как у детектора."""
    cmd = as_source(video).cmd("ffmpeg", ["-hide_banner", "-loglevel", "error", "-nostdin", "-i", "{}",
                                          "-t", str(seconds),
                                          "-vf", f"fps={p.fps},scale={p.width}:{p.height}:flags=area,format=gray",
                                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], compress=True)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    size = p.width * p.height
    n = len(r.stdout) // size
    if n == 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip()[-300:] or "ffmpeg не отдал кадров")
    return np.frombuffer(r.stdout[: n * size], dtype=np.uint8).reshape(n, p.height, p.width)


def corr(a: np.ndarray, b: np.ndarray) -> float:
    """Насколько два кадра — ОДНА И ТА ЖЕ картинка (корреляция яркостей, 1 — совпадают).
    Разгорающийся из черноты кадр коррелирует со своим ярким видом почти единицей; соседние
    кадры разных сцен — около нуля."""
    x = a.astype(np.float32).ravel()
    y = b.astype(np.float32).ravel()
    x -= x.mean()
    y -= y.mean()
    return float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9))


def first_visible(frames: np.ndarray, p: Params, dark: float = DARK, flat: float = FLAT,
                  same: float = SAME, rise: float = RISE) -> int | None:
    """Индекс первого кадра, который ВИДНО: не тёмный и с содержимым. Дальше — только пока
    картинка ТА ЖЕ и продолжает разгораться (наплыв из черноты): тогда берём её проявленной.

    ⚠️ Неподвижности требовать НЕЛЬЗЯ, и «яркость устоялась» — тоже (ловилось 14.09 на первой
    же конференционной записи, дважды). Титульную карточку делают ЖИВОЙ: докладчик в кадре
    улыбается, элементы летят, а сама карточка через секунду уходит кроссфейдом в план зала.
    Правило «следующий кадр почти такой же» увело выбор на 29-ю секунду, правило «яркость
    больше не едет» — на середину кроссфейда, где заголовок просвечивал сквозь зал. Заставка —
    это ПЕРВЫЙ кадр, и единственное, что с ним делаем, — доводим до полной яркости, если видео
    открывается наплывом из черноты (там соседние кадры — та же картинка, только ярче).
    """
    n = len(frames)
    for i in range(n):
        if float(frames[i].mean()) < dark or float(frames[i].std()) < flat:
            continue
        while i + 1 < n and corr(frames[i], frames[i + 1]) >= same \
                and float(frames[i + 1].mean()) > float(frames[i].mean()) + rise:
            i += 1
        return i
    return None


def crop_from_layout(layout: dict | None, margin: float = 0.01) -> str:
    """Та же рамка, что у остальных кадров записи (`layout.bbox` в долях кадра, то же поле, что у
    `crop_expr`): слайдшоу на карточке показывает все кадры через одну рамку обложки."""
    bx0, by0, bx1, by1 = (layout or {}).get("bbox") or [0.0, 0.0, 1.0, 1.0]
    fx0, fy0 = max(0.0, bx0 - margin), max(0.0, by0 - margin)
    fx1, fy1 = min(1.0, bx1 + margin), min(1.0, by1 + margin)
    return f"crop=iw*{fx1 - fx0:.4f}:ih*{fy1 - fy0:.4f}:iw*{fx0:.4f}:ih*{fy0:.4f}"


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def process(video: "Path | str | Source", rec: Path, seconds: int = INTRO_SEC, dry: bool = False,
            redo: bool = False) -> str:
    path = rec / "record.slides.json"
    if not path.is_file():
        return "нет шкалы — сначала slides_from_video.py"
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {f for f in Params.__dataclass_fields__}
    p = Params(**{k: v for k, v in (data.get("params") or {}).items() if k in known})
    old = data.get("intro") or {}

    def drop(reason: str) -> str:
        if "intro" in data:
            del data["intro"]
            (rec / FRAME).unlink(missing_ok=True)
            if not dry:
                _write(path, data)
        return reason

    frames = head_frames(video, p, seconds)
    i = first_visible(frames, p)
    if i is None:
        return drop(f"видимого кадра в первых {seconds} с нет")
    t = round(i / p.fps, 1)
    first = min((float(s.get("t0") or 0) for s in data.get("slides") or []), default=None)
    if first is not None and first <= t + 3:
        return drop(f"первый слайд шкалы начинается на {first} с — заставка уже в кандидатах")
    if not redo:
        if old.get("t") == t and old.get("people") and "who" in old:
            return f"на первом кадре люди ({old['who']}) — уже проверено"
        if old.get("t") == t and old.get("frame") == FRAME and (rec / FRAME).is_file():
            return f"без изменений: {FRAME} ({t} с)"
    if dry:
        return f"кадр на {t} с (dry)"
    # ⚠️ Кадр берётся ЦЕЛИКОМ, без рамки содержимого записи: рамка меряется по тому, где на
    # экране идут смены слайдов, и у конференционной съёмки она срезала у титульной карточки
    # заголовок (ловилось 14.09). Полосу участников, если она на заставке есть, отрежет
    # проверка Vision при сборке обложки (`make_cover --render`).
    if not save_frame(as_source(video), t, rec / FRAME, crop_from_layout(None), p.frame_width):
        return f"кадр на {t} с не вырезался"
    # описание прежнего кадра к новой секунде не относится
    data["intro"] = {"t": t, "frame": FRAME,
                     "mean": round(float(frames[i].mean()), 1), "std": round(float(frames[i].std()), 1)}
    _write(path, data)
    return f"заставка на {t} с → {FRAME}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", help="файл видео; с --remote — путь на сервере")
    ap.add_argument("--remote", metavar="HOST", help="видео на сервере (user@host): ffmpeg по ssh")
    ap.add_argument("--record", type=Path, help="каталог записи с record.slides.json")
    ap.add_argument("--all", action="store_true", help="весь корпус: видео по `media:` шапки (нужен --remote)")
    ap.add_argument("--seconds", type=int, default=INTRO_SEC)
    ap.add_argument("--redo", action="store_true", help="вырезать заново и там, где кадр уже есть или был удалён как люди")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.all:
        if not a.remote:
            ap.error("--all работает с --remote: видео лежат только на сервере")
        from make_slides_md import read_header
        from video_batch import server_path
        import spaces
        recs = [d for d in spaces.record_dirs() if (d / "record.slides.json").is_file()]
        t0 = time.time()
        for k, rec in enumerate(recs, 1):
            media = read_header(rec / "record.md").get("media", "").strip().strip('"')  # шапка хранит путь в кавычках
            if not media:
                print(f"[{k}/{len(recs)}] {rec.name}: видео нет")
                continue
            try:
                out = process(Source(server_path(media), a.remote), rec, a.seconds, a.dry, a.redo)
            except Exception as e:  # noqa: BLE001 — одна запись не должна ронять обход
                out = f"СБОЙ: {e}"
            print(f"[{k}/{len(recs)}] {rec.name}: {out}", flush=True)
        print(f"готово за {time.time() - t0:.0f} с")
        return 0
    if not a.video or not a.record:
        ap.error("укажите видео и --record, либо --all --remote HOST")
    print(process(Source(a.video, a.remote), a.record, a.seconds, a.dry, a.redo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
