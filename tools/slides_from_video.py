#!/usr/bin/env python3
"""Шкала экрана из видео записи: где стабильный слайд, где живой экран, ключевые кадры.

    python3 tools/slides_from_video.py ~/asr-stack/video/<id>.mp4 --record <каталог записи>
    python3 tools/slides_from_video.py <видео> --out /tmp/x        # без записи, в любой каталог
    python3 tools/slides_from_video.py <видео> --record … --no-frames   # только шкала, без jpeg

Выход — `record.slides.json` (шкала: участки `slide`/`active`, слайды с ключевым кадром и
тождеством, выборка кадров из активных участков) и каталог `slides/` с кадрами для
Vision-модели (`describe_slides.py`). Модель здесь НЕ вызывается, всё считается локально.

Как это работает (замерено 12.09.2026 на семи записях из четырёх веток, см. docs/video-mvp.md):
- ffmpeg отдаёт серые кадры 320×180 раз в секунду в трубу. ⚠️ ПРОГРАММНЫЙ декодер, без
  `-hwaccel videotoolbox`: при таком маленьком выходе аппаратный в ДЕСЯТЬ раз медленнее
  (29× против 277-309× реального времени на h264 1080p) — узкое место не декодирование,
  а выгрузка кадров с GPU. VP8 4K с экрана — 41-48×.
- Маска НЕ-содержимого строится из самого видео: (1) рамка «где хоть раз менялось много» —
  настоящая смена слайда перекрашивает большую площадь, полоса участников конференции
  никогда; (2) часто меняющиеся пиксели (вебкам-окно, часы). Без маски ложных разрезов
  было 18 из 70 на одной записи, и все — на 100 % в полосе участников.
- Кадр «активен», если вне маски изменилось больше `thr` пикселей. Стабильный участок
  короче `min_slide` — не слайд, а часть активности (пауза при наборе кода, мелькнувший
  слайд); активность короче `min_dynamic` — переход между слайдами.
- «Билд» (пункты слайда появляются по одному) склеивается: изменившиеся пиксели в основном
  ПОЯВИЛИСЬ, а не исчезли; ключевой кадр — последнее состояние.
- Тождество слайда при возврате — прямым сравнением ключевых кадров после нормализации
  (обрезка до самого слайда + перебор сдвига). dHash на 64 бита склеивал разные слайды
  одной вёрстки; а Jitsi/Meet перерисовывают область показа при открытии панели — тот же
  слайд на 13-16 % пикселей «другой». Остальные дубли (слайд в редакторе PowerPoint,
  всплывшее уведомление поверх) — второй ярус, по тексту после описания.
- ⚠️ «Люди против экрана» по одному движению НЕ различаются: галерея лиц 0.21-0.36 разброса,
  прокрутка сайта 0.40, камера 0.5-0.6. Поэтому рода два — `slide` и `active`, признаки
  движения сохраняются как подсказка, а «это люди» решает Vision по кадру и такой кадр
  не описывается.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter

VERSION = "slides-v1"


@dataclasses.dataclass
class Params:
    fps: float = 1.0         # кадров в секунду на анализ
    width: int = 320         # размер кадра на анализ (высота — 180)
    height: int = 180
    delta: int = 24          # |Δ| яркости, с которого пиксель считается изменившимся
    thr: float = 0.02        # доля изменившихся пикселей (вне маски), с которой кадр «активен»
    stable: int = 2          # затишье короче — не стабильный участок, а часть перехода
    min_slide: int = 8       # стабильный участок короче (с) — не слайд
    min_dynamic: int = 10    # активность короче (с) — переход, а не живой экран
    freq_delta: int = 6      # маска частых изменений: слабый порог…
    freq_k: float = 3.0      # …и частота выше k × медианы (не ниже freq_min)
    freq_min: float = 0.02
    freq_parts: int = 6      # отрезков записи для медианы частоты (не короче минуты каждый)
    big: float = 0.15        # смена «крупная», если поменялось больше этой доли кадра
    bbox_density: float = 0.2
    build_max: float = 0.45  # билд: изменилось не больше этой доли содержимого…
    build_gone: float = 0.12 # …и «исчезнувших» пикселей среди изменившихся не больше этой доли
    same_share: float = 0.03 # нормализованные ключевые кадры отличаются меньше → тот же слайд
    active_every: int = 60   # выборка кадров из активных участков, секунд
    frame_width: int = 1280  # ширина jpeg для модели


# --- кадры ----------------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Source:
    """Видео: локальный файл или файл НА СЕРВЕРЕ (`host` задан) — тогда ffmpeg/ffprobe идут по ssh, а
    сюда едет только результат: серый поток кадров для детектора и jpeg-и выбранных секунд.

    Зачем (13.09): батч по корпусу упёрся в сеть — осталось 108 ГиБ видео при ~3 МБ/с (11 часов),
    а поток 320×180 в 1 к/с — 57 КБ/с сырых, в 20-50 раз меньше файла, и ещё жмётся `ssh -C`
    (серые слайды почти не меняются). Сервер декодирует 1080p в ~80× реального времени (замерено:
    10 минут за 7 с), то есть час видео — под минуту. Своего питона с numpy на сервере нет и не
    нужно: там только ffmpeg 4.4 (⚠️ у него `-vsync`, а не `-fps_mode`)."""
    path: str
    host: str | None = None

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def remote(self) -> bool:
        return bool(self.host)

    def is_file(self) -> bool:
        return self.remote or Path(self.path).is_file()

    def cmd(self, tool: str, args: list[str], compress: bool = False) -> list[str]:
        """Команда ffmpeg/ffprobe; `{}` в args — место пути. На сервере — одной строкой через ssh."""
        argv = [tool, *(self.path if x == "{}" else x for x in args)]
        if not self.remote:
            return argv
        return ["ssh", *(["-C"] if compress else []), "-o", "BatchMode=yes", self.host, shlex.join(argv)]


def as_source(video: "Path | str | Source") -> Source:
    return video if isinstance(video, Source) else Source(str(video))


def probe(video: "Path | Source") -> dict:
    """Что за видео: кодек, размер кадра, частота, длительность.

    ⚠️ Без `ffprobe` тоже обязано работать. На маках коллег стоит установка с зеркала сайта, а
    там лежит СТАТИЧЕСКИЙ `ffmpeg` из колеса `imageio-ffmpeg` — в нём `ffprobe` не идёт вовсе
    (готовых статических сборок `ffprobe` под arm64 в PyPI нет ни одной). Запасной ход — `av`:
    то же ffmpeg, но библиотекой, и колесо под arm64 есть. Порядок именно такой: где `ffprobe`
    стоит (ноутбук, сервер), поведение байт в байт прежнее.
    """
    src = as_source(video)
    if not src.remote and not shutil.which("ffprobe"):
        return _probe_av(Path(src.path))
    out = subprocess.run(
        src.cmd("ffprobe", ["-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name,width,height,r_frame_rate",
                            "-show_entries", "format=duration", "-of", "json", "{}"]),
        capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    st = (j.get("streams") or [{}])[0]
    return {"codec": st.get("codec_name"), "width": st.get("width"), "height": st.get("height"),
            "rate": st.get("r_frame_rate"), "duration": float(j.get("format", {}).get("duration") or 0)}


def _probe_av(path: Path) -> dict:
    """То же самое библиотекой `av` (PyAV) — когда `ffprobe` на машине нет."""
    try:
        import av  # noqa: PLC0415 — только для этого случая, в общий импорт не тянем
    except ImportError:
        raise SystemExit("нет ни ffprobe, ни пакета av — поставьте av (tools/requirements-video.txt)")
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.base_rate
        return {"codec": stream.codec_context.name, "width": stream.codec_context.width,
                "height": stream.codec_context.height,
                "rate": f"{rate.numerator}/{rate.denominator}" if rate else "",
                "duration": float(container.duration / av.time_base) if container.duration else 0.0}


def extract_frames(video: "Path | Source", p: Params) -> np.ndarray:
    """Серые кадры (N, H, W) uint8 через трубу; ⚠️ без hwaccel — см. шапку модуля."""
    cmd = as_source(video).cmd("ffmpeg", ["-hide_banner", "-loglevel", "error", "-nostdin", "-i", "{}",
                                          "-vf", f"fps={p.fps},scale={p.width}:{p.height}:flags=area,format=gray",
                                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], compress=True)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode:
        sys.exit(f"ffmpeg: {r.stderr.decode(errors='replace')[-400:]}")
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(buf) // (p.width * p.height)
    return buf[: n * p.width * p.height].reshape(n, p.height, p.width)


# --- маска и активность ---------------------------------------------------------------------

def layout_mask(frames: np.ndarray, p: Params) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Маска НЕ-содержимого: всё вне рамки крупных изменений плюс часто меняющиеся пиксели."""
    f = frames.astype(np.int16)
    d = np.abs(np.diff(f, axis=0))
    n, h, w = frames.shape
    # Частота — МЕДИАНА по отрезкам записи, а не среднее за всё время: полоса участников и
    # вебкам меняются на каждом отрезке, а область демо — на одном-двух, и по среднему она
    # попала бы в маску, ослепив детектор к сменам там на весь остальной доклад.
    parts = max(1, min(p.freq_parts, (n - 1) // 60))
    chunks = np.array_split(d > p.freq_delta, parts, axis=0)
    freq = uniform_filter(np.median(np.stack([c.mean(axis=0) for c in chunks]), axis=0), size=9)
    fmask = freq > max(p.freq_min, p.freq_k * float(np.median(freq)))
    big = (d > p.delta).reshape(n - 1, -1).mean(axis=1) > p.big
    x0, x1, y0, y1 = 0, w, 0, h
    if big.sum() >= 3:
        union = (d[big] > p.delta).mean(axis=0) > p.bbox_density
        cols = np.where(union.mean(axis=0) > p.bbox_density)[0]
        rows = np.where(union.mean(axis=1) > p.bbox_density)[0]
        if len(cols) and len(rows):
            x0, x1, y0, y1 = int(cols.min()), int(cols.max()) + 1, int(rows.min()), int(rows.max()) + 1
    content = np.zeros((h, w), bool)
    content[y0:y1, x0:x1] = True
    return (~content) | fmask, (x0, y0, x1, y1)


def activity(frames: np.ndarray, mask: np.ndarray, p: Params) -> np.ndarray:
    """change[i] — доля изменившихся пикселей вне маски между кадрами i и i+1."""
    d = np.abs(np.diff(frames.astype(np.int16), axis=0)) > p.delta
    return d[:, ~mask].mean(axis=1)


def label_frames(change: np.ndarray, p: Params) -> np.ndarray:
    """True — кадр активен. Затишье короче `stable` и стабильные куски короче `min_slide`
    считаются активностью."""
    n = len(change) + 1
    act = np.zeros(n, bool)
    act[1:] = change > p.thr
    for min_len in (p.stable, p.min_slide):
        s = 0
        for i in range(1, n + 1):
            if i == n or act[i] != act[s]:
                if not act[s] and i - s < min_len:
                    act[s:i] = True
                s = i
    return act


def motion(frames: np.ndarray, mask: np.ndarray, s: int, e: int, p: Params, grid=(9, 16)) -> dict:
    """Признаки движения активного участка — подсказка, не решение (см. шапку модуля)."""
    f = frames[s:e].astype(np.int16)
    if len(f) < 2:
        return {"share": 0.0, "spread": 0.0}
    d = np.abs(np.diff(f, axis=0)) > p.delta
    d[:, mask] = False
    n, h, w = d.shape
    gh, gw = grid
    blocks = d[:, :h // gh * gh, :w // gw * gw].reshape(n, gh, h // gh, gw, w // gw).mean(axis=(2, 4)) > 0.01
    return {"share": round(float(d.reshape(n, -1).mean()), 3), "spread": round(float(blocks.mean()), 3)}


def segment(frames: np.ndarray, mask: np.ndarray, change: np.ndarray, p: Params) -> list[dict]:
    """Участки по индексам кадров: slide (стабильный ≥ min_slide) и active (≥ min_dynamic);
    короткая активность — переход, приписывается следующему участку."""
    act = label_frames(change, p)
    n = len(act)
    segs: list[dict] = []
    pending = None
    s = 0
    for i in range(1, n + 1):
        if i == n or act[i] != act[s]:
            if not act[s]:
                seg = {"kind": "slide", "i0": s, "i1": i}
                if pending:
                    seg["transition"] = pending
                    pending = None
                segs.append(seg)
            elif i - s < p.min_dynamic:
                pending = [s, i]
            else:
                seg = {"kind": "active", "i0": pending[0] if pending else s, "i1": i}
                pending = None
                seg["motion"] = motion(frames, mask, seg["i0"], i, p)
                segs.append(seg)
            s = i
    if pending:
        segs.append({"kind": "active", "i0": pending[0], "i1": pending[1],
                     "motion": motion(frames, mask, pending[0], pending[1], p)})
    return segs


# --- слайды ----------------------------------------------------------------------------------

def is_build(a: np.ndarray, b: np.ndarray, bbox, p: Params) -> bool:
    """B — «достроенный» A: изменившиеся пиксели в основном появились (ушли от фона)."""
    x0, y0, x1, y1 = bbox
    A = a[y0:y1, x0:x1].astype(np.int16)
    B = b[y0:y1, x0:x1].astype(np.int16)
    changed = np.abs(B - A) > p.delta
    share = changed.mean()
    if share == 0 or share > p.build_max:
        return False
    bg = int(np.bincount(A.flatten()).argmax())
    gone = (np.abs(B - bg) < np.abs(A - bg) - p.delta // 2) & changed
    return gone.sum() / changed.sum() < p.build_gone


def content_crop(img: np.ndarray, tol: int = 20, share: float = 0.4) -> np.ndarray:
    """Прямоугольник самого слайда внутри области показа: строки и столбцы, где не меньше
    `share` пикселей близки к моде яркости (фону слайда). У тёмного слайда на тёмном фоне
    конференции границы не найдутся — тогда область целиком."""
    im = img.astype(np.int16)
    bg = int(np.bincount(img.flatten()).argmax())
    like = np.abs(im - bg) < tol
    rows = np.where(like.mean(axis=1) > share)[0]
    cols = np.where(like.mean(axis=0) > share)[0]
    if len(rows) < 8 or len(cols) < 8:
        return img
    return img[rows.min():rows.max() + 1, cols.min():cols.max() + 1]


def normalized(img: np.ndarray, size=(256, 144)) -> np.ndarray:
    return np.asarray(Image.fromarray(content_crop(img)).resize(size, Image.BILINEAR), dtype=np.int16)


def shifted_diff(A: np.ndarray, B: np.ndarray, p: Params, maxs: int = 3) -> float:
    """Минимальная доля отличий по сдвигам ±maxs пикселей."""
    best = 1.0
    for dx in range(-maxs, maxs + 1):
        for dy in range(-maxs, maxs + 1):
            a = A[max(0, dy):A.shape[0] + min(0, dy), max(0, dx):A.shape[1] + min(0, dx)]
            b = B[max(0, -dy):B.shape[0] + min(0, -dy), max(0, -dx):B.shape[1] + min(0, -dx)]
            best = min(best, float((np.abs(a - b) > p.delta).mean()))
    return best


def dhash(img: np.ndarray, w: int = 9, h: int = 8) -> str:
    small = np.asarray(Image.fromarray(img).resize((w, h), Image.BOX), dtype=np.int16)
    return "".join("1" if b else "0" for b in (small[:, 1:] > small[:, :-1]).flatten())


def slides_of(frames: np.ndarray, segs: list[dict], bbox, p: Params) -> list[dict]:
    """Слайды из стабильных участков: билды склеены, тождество при возврате по нормализованным
    кадрам. Времена — в индексах кадров, в секунды переводит `to_seconds`."""
    x0, y0, x1, y1 = bbox
    out: list[dict] = []
    for k, s in enumerate(segs):
        if s["kind"] != "slide":
            continue
        mid = (s["i0"] + s["i1"]) // 2
        if out and out[-1]["i1"] + 8 >= s["i0"] and not any(
                t["kind"] != "slide" and t["i0"] >= out[-1]["i1"] and t["i1"] <= s["i0"] for t in segs):
            prev = out[-1]
            if is_build(frames[prev["key"]], frames[mid], bbox, p):
                prev["builds"].append(s["i0"])
                prev["i1"] = s["i1"]
                prev["key"] = mid
                continue
        out.append({"i0": s["i0"], "i1": s["i1"], "key": mid, "builds": [], "segment": k})
    # тождество: сначала грубо (нормализованный кадр 256×144, прямое сравнение), затем сдвиг
    norms = [normalized(frames[e["key"]][y0:y1, x0:x1]) for e in out]
    ids: list[int] = []
    for k, K in enumerate(norms):
        same = None
        for j in range(k):
            if float((np.abs(norms[j] - K) > p.delta).mean()) < 0.25 and \
                    shifted_diff(norms[j], K, p) < p.same_share:
                same = ids[j]
                break
        ids.append(same if same is not None else (max(ids) + 1 if ids else 1))
    for e, i in zip(out, ids):
        e["slide"] = i
        e["hash"] = dhash(frames[e["key"]][y0:y1, x0:x1])
    return out


# --- ключевые кадры ---------------------------------------------------------------------------

def crop_expr(bbox, p: Params, margin: float = 0.01) -> str:
    """Выражение ffmpeg crop в долях кадра: рамка содержимого с полем, без полосы участников."""
    x0, y0, x1, y1 = bbox
    fx0, fy0 = max(0.0, x0 / p.width - margin), max(0.0, y0 / p.height - margin)
    fx1, fy1 = min(1.0, x1 / p.width + margin), min(1.0, y1 / p.height + margin)
    return f"crop=iw*{fx1 - fx0:.4f}:ih*{fy1 - fy0:.4f}:iw*{fx0:.4f}:ih*{fy0:.4f}"


def _grab_args(t: float, crop: str, width: int, remote: bool) -> list[str]:
    # ⚠️ ffmpeg 4.4 на сервере знает `-vsync`, локальный 8.x — `-fps_mode`; смысл один: passthrough
    return ["-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", "{}",
            "-frames:v", "1", *(["-vsync", "passthrough"] if remote else ["-fps_mode", "passthrough"]),
            "-vf", f"{crop},scale={width}:-2", "-q:v", "4"]


def save_frame(video: "Path | Source", t: float, out: Path, crop: str, width: int) -> bool:
    """Один кадр в момент t. ⚠️ Успех — по ФАЙЛУ, а не по коду возврата: у webm из браузера
    частота кадров переменная, на статичном слайде кадров в потоке нет вовсе, и ffmpeg,
    заполнив дыру дубликатами (`dup=1457`), пишет кадр и всё равно отвечает «Conversion
    failed». Так пропали 17 слайдов из 54 на одной записи.
    На сервере кадр едет через трубу (`image2pipe`) — файл пишется здесь, тем же правилом."""
    src = as_source(video)
    out.unlink(missing_ok=True)
    if src.remote:
        r = subprocess.run(src.cmd("ffmpeg", [*_grab_args(t, crop, width, True), "-f", "image2pipe", "-c:v", "mjpeg", "-"]),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if len(r.stdout) > 1024:
            out.write_bytes(r.stdout)
    else:
        subprocess.run(src.cmd("ffmpeg", [*_grab_args(t, crop, width, False), str(out)]), capture_output=True)
    return out.exists() and out.stat().st_size > 1024


def save_frames_remote(src: Source, items: list[tuple[float, str]], frames_dir: Path, crop: str, width: int) -> None:
    """Все кадры записи ОДНИМ заходом: цикл ffmpeg на сервере во временный каталог → один rsync →
    уборка. По одному ssh на кадр — это 40-60 рукопожатий и секунд на запись; так — два."""
    tmp = f"/tmp/morag-frames/{uuid.uuid4().hex}"
    args = _grab_args(0, crop, width, True)
    args[args.index("-ss") + 1] = "SSTIME"      # ⚠️ подстановка после shlex.join: `$t` и `{}` он бы заквотил
    args[args.index("{}")] = src.path
    ff = shlex.join(["ffmpeg", *args]).replace(" -ss SSTIME ", ' -ss "$t" ')
    loop = f"mkdir -p {tmp} && while read -r t name; do {ff} \"{tmp}/$name\" </dev/null; done"
    plan = "".join(f"{t:.3f} {name}\n" for t, name in items)
    subprocess.run(["ssh", "-o", "BatchMode=yes", src.host, loop], input=plan, text=True, capture_output=True)
    subprocess.run(["rsync", "-a", f"{src.host}:{tmp}/", str(frames_dir) + "/"], capture_output=True)
    subprocess.run(["ssh", "-o", "BatchMode=yes", src.host, f"rm -rf {tmp}"], capture_output=True)


# --- сборка ----------------------------------------------------------------------------------

def analyze(video: Path, p: Params, log=print) -> tuple[dict, np.ndarray, tuple]:
    info = probe(video)
    t0 = time.time()
    frames = extract_frames(video, p)
    t_dec = time.time() - t0
    log(f"кадров {len(frames)} ({len(frames) / p.fps / 60:.0f} мин) за {t_dec:.1f} с "
        f"— {len(frames) / p.fps / max(t_dec, 1e-6):.0f}× реального времени, {info['codec']} "
        f"{info['width']}x{info['height']}")
    t0 = time.time()
    mask, bbox = layout_mask(frames, p)
    change = activity(frames, mask, p)
    segs = segment(frames, mask, change, p)
    sl = slides_of(frames, segs, bbox, p)
    sec = lambda i: round(i / p.fps, 1)  # noqa: E731
    n = len(frames)
    tot = {"slide": 0, "active": 0}
    for s in segs:
        tot[s["kind"]] += s["i1"] - s["i0"]
    samples = []
    for k, s in enumerate(segs):
        if s["kind"] != "active":
            continue
        t = s["i0"] + p.active_every // 2
        while t < s["i1"]:
            samples.append({"t": sec(t), "segment": k})
            t += p.active_every
    result = {
        "version": VERSION,
        "source": {"video": as_source(video).name, "duration": round(info["duration"], 1), "codec": info["codec"],
                   "size": [info["width"], info["height"]], "rate": info["rate"]},
        "params": dataclasses.asdict(p),
        "layout": {"bbox": [round(bbox[0] / p.width, 4), round(bbox[1] / p.height, 4),
                            round(bbox[2] / p.width, 4), round(bbox[3] / p.height, 4)],
                   "mask_share": round(float(mask.mean()), 3)},
        "summary": {"slide_share": round(tot["slide"] / n, 3), "active_share": round(tot["active"] / n, 3),
                    "slides": len(sl), "unique": len({e["slide"] for e in sl}),
                    "builds": sum(len(e["builds"]) for e in sl), "samples": len(samples),
                    "decode_sec": round(t_dec, 1), "analyze_sec": round(time.time() - t0, 1)},
        "segments": [{"kind": s["kind"], "t0": sec(s["i0"]), "t1": sec(s["i1"]),
                      **({"transition": [sec(s["transition"][0]), sec(s["transition"][1])]} if "transition" in s else {}),
                      **({"motion": s["motion"]} if "motion" in s else {})} for s in segs],
        "slides": [{"n": k + 1, "slide": e["slide"], "t0": sec(e["i0"]), "t1": sec(e["i1"]),
                    "t_key": sec(e["key"]), "builds": [sec(b) for b in e["builds"]],
                    "hash": e["hash"], "segment": e["segment"]} for k, e in enumerate(sl)],
        "samples": [{"n": k + 1, **s} for k, s in enumerate(samples)],
    }
    return result, frames, bbox


def carry_over(result: dict, old: dict) -> int:
    """Перенести описания модели из прежнего сайдкара: слайд тот же, если совпали ключевой
    кадр и хэш (выборка — момент). Повторный прогон шкалы не должен стоить повторных вызовов
    Vision — они дорогие, а шкала пересчитывается за секунды."""
    keep = ("desc", "desc_meta", "people", "desc_error")
    by_key = {(round(e["t_key"], 1), e.get("hash")): e for e in old.get("slides", [])}
    by_t = {round(s["t"], 1): s for s in old.get("samples", [])}
    n = 0
    for e in result["slides"]:
        src = by_key.get((round(e["t_key"], 1), e.get("hash")))
        if src and (src.get("desc") or src.get("people")):
            e.update({k: src[k] for k in keep if k in src})
            n += 1
    for s in result["samples"]:
        src = by_t.get(round(s["t"], 1))
        if src and (src.get("desc") or src.get("people")):
            s.update({k: src[k] for k in keep if k in src})
            n += 1
    return n


def write_frames(video: "Path | Source", result: dict, out_dir: Path, bbox, p: Params, log=print) -> None:
    src = as_source(video)
    frames_dir = out_dir / "slides"
    frames_dir.mkdir(parents=True, exist_ok=True)
    crop = crop_expr(bbox, p)
    t0 = time.time()
    done = 0
    # ⚠️ Кадры, на которых модель уже увидела чужие лица, заново НЕ вырезаем: их удалили
    # намеренно. Докладчик (`who: speaker`) — не чужое лицо, его кадр остаётся и режется снова.
    keep = lambda e: not e.get("people") or e.get("who") == "speaker"  # noqa: E731
    wanted = [(e["t_key"], f"s{e['n']:03d}.jpg", e) for e in result["slides"] if keep(e)] + \
             [(s["t"], f"a{s['n']:03d}.jpg", s) for s in result["samples"] if keep(s)]
    if src.remote:
        for _, name, _ in wanted:
            (frames_dir / name).unlink(missing_ok=True)
        save_frames_remote(src, [(t, name) for t, name, _ in wanted], frames_dir, crop, p.frame_width)
    for t, name, e in wanted:
        f = frames_dir / name
        ok = (f.is_file() and f.stat().st_size > 1024) if src.remote else save_frame(src, t, f, crop, p.frame_width)
        if ok:
            e["frame"] = f"slides/{name}"
            done += 1
    # осиротевшие кадры прежнего прогона (номера сдвинулись) — прочь, чтобы не лежали лица.
    # Заставка (`intro_frame.py`) и обложка — не сироты: они живут вне `slides[]`.
    live = {e.get("frame") for e in result["slides"]} | {s.get("frame") for s in result["samples"]} \
        | {(result.get("intro") or {}).get("frame"), (result.get("cover") or {}).get("frame")}
    for f in frames_dir.glob("*.jpg"):
        if f"slides/{f.name}" not in live:
            f.unlink()
    log(f"кадров сохранено {done} за {time.time() - t0:.1f} с → {frames_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="файл видео; с --remote — путь на сервере")
    ap.add_argument("--remote", metavar="HOST", help="видео лежит на сервере (user@host): ffmpeg идёт по ssh, качается только поток кадров")
    ap.add_argument("--record", type=Path, help="каталог записи: сюда лягут record.slides.json и slides/")
    ap.add_argument("--out", type=Path, help="каталог вывода, если не в запись")
    ap.add_argument("--no-frames", action="store_true", help="только шкала, jpeg не сохранять")
    ap.add_argument("--json", action="store_true", help="напечатать сводку JSON")
    defaults = Params()
    for f in dataclasses.fields(Params):
        ap.add_argument(f"--{f.name.replace('_', '-')}", type=f.type if f.type in (int, float) else float,
                        default=getattr(defaults, f.name), help=f"по умолчанию {getattr(defaults, f.name)}")
    a = ap.parse_args()
    out_dir = a.record or a.out
    if not out_dir:
        sys.exit("укажите --record <каталог записи> или --out <каталог>")
    src = Source(a.video, a.remote)
    if not src.is_file():
        sys.exit(f"нет файла {a.video}")
    p = Params(**{f.name: getattr(a, f.name) for f in dataclasses.fields(Params)})
    result, _, bbox = analyze(src, p)
    s = result["summary"]
    print(f"слайд {s['slide_share'] * 100:.0f}% · активно {s['active_share'] * 100:.0f}% · "
          f"слайдов {s['slides']} (уникальных {s['unique']}, билдов {s['builds']}) · "
          f"выборка из активных {s['samples']} · рамка {result['layout']['bbox']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "record.slides.json"
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        carried = carry_over(result, old)
        if carried:
            print(f"перенесено описаний из прежнего сайдкара: {carried}")
        # ⚠️ Шкала пересчитывается целиком, но обложка (в том числе рука владельца, `by: owner`)
        # и заставка (`intro_frame.py`) к шкале не относятся — переносим как есть. До 14.09 повторный
        # прогон детектора молча терял `cover`.
        for key in ("cover", "intro"):
            if key in old:
                result[key] = old[key]
    if not a.no_frames:
        write_frames(src, result, out_dir, bbox, p)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"→ {path}")
    if a.json:
        print(json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
