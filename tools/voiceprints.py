#!/usr/bin/env python3
"""Отпечатки голосов записи: 192 числа на голос — по ним СЕРВЕР узнаёт, кто говорит.

    python3 tools/voiceprints.py <артефакт.json> <звук> -o voices.json

Зачем. Реестр голосов — состояние корпуса, и живёт он там, где корпус: на сервере. Машине, где
идёт расшифровка, его не отдают (это биометрия трёхсот человек, и две копии неизбежно
разъедутся). Но узнать голос можно и не имея реестра: достаточно прислать отпечаток, а решение —
«это Speaker_19» или «это новый голос» — принимает сервер (`app/content/registry.py`).

Отпечаток считает CAM++ — он и так поднят на машине, где шла расшифровка, тем же стеком. Здесь
только сборка спанов из артефакта и один HTTP-запрос: `POST /embed-centroids`, multipart
(wav 16 кГц моно + спаны), ответ — `{centroids: {метка: [192]}, air: {метка: секунды}}`.

⚠️ Спаны берутся ПО СЕГМЕНТАМ реплик, а не по их границам: между сегментами одной реплики бывают
паузы и чужая речь, и центроид, посчитанный по границам, вбирает соседа. Так же считает
`rebuild_registry.py`, которым пересобирался реестр корпуса, — расходиться с ним нельзя.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SPEAKER_RE = re.compile(r"^Speaker_\d+$")
MIN_SEG = 2.0          # короче CAM++ в центроид всё равно не берёт
DEFAULT_URL = "http://127.0.0.1:8126/embed-centroids"


def spans_of(artifact: dict) -> dict[str, dict]:
    """Метка голоса → его куски речи и эфир. Только безымянные `Speaker_N`: у названного голоса
    метка уже человеческая, и узнавать его незачем."""
    x = artifact.get("x_enriched") or artifact
    out: dict[str, dict] = {}
    for turn in x.get("turns") or []:
        label = str(turn.get("speaker_id") or turn.get("speaker") or "")
        if not SPEAKER_RE.match(label):
            continue
        segs = [(float(s["start"]), float(s["end"])) for s in (turn.get("segments") or [])
                if float(s.get("end", 0)) > float(s.get("start", 0))]
        if not segs and float(turn.get("end", 0)) > float(turn.get("start", 0)):
            segs = [(float(turn["start"]), float(turn["end"]))]
        rec = out.setdefault(label, {"spans": [], "air_sec": 0.0, "longest": 0.0})
        rec["spans"].extend(segs)
        rec["air_sec"] += sum(e - s for s, e in segs)
        rec["longest"] = max([rec["longest"]] + [e - s for s, e in segs])
    return out


def wav16k(src: Path, out: Path) -> Path:
    """Звук в том виде, в каком его ждёт CAM++ (16 кГц моно wav). Уже есть — не пересчитываем."""
    if out.is_file() and out.stat().st_size:
        return out
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-vn", "-ar", "16000", "-ac", "1", str(out)], check=True)
    return out


def embed(wav: Path, spans: list[dict], url: str, key: str = "", timeout: float = 600) -> dict:
    """Один запрос к CAM++. Multipart собираем руками: клиенту не нужен requests ради трёх полей."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"spans\"\r\n\r\n".encode()
    body += json.dumps(spans).encode() + b"\r\n"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{wav.name}\"\r\n"
             f"Content-Type: audio/wav\r\n\r\n").encode()
    body += wav.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # ⚠️ Прокси из окружения снимаем: CAM++ слушает петлю, а корпоративный прокси на неё не ходит.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=bytes(body), headers=headers)
    with opener.open(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8"))


def fingerprints(artifact: Path, audio: Path, *, url: str = DEFAULT_URL, key: str = "",
                 work: Path | None = None) -> dict:
    """Отпечатки голосов записи: `{метка: {centroid, air_sec, cluster}}`.

    Голос без куска длиннее двух секунд отпечатка не получит — CAM++ такие пропускает. Это не
    ошибка: на сервере такая метка отойдёт самому длинному голосу записи, как делает и конвейер.
    """
    data = json.loads(Path(artifact).read_text(encoding="utf-8"))
    voices = spans_of(data)
    if not voices:
        return {}
    wav = wav16k(Path(audio), (work or Path(artifact).parent) / "voices.wav")
    spans = [{"start": s, "end": e, "speaker": label}
             for label, rec in voices.items() for s, e in rec["spans"] if e - s >= MIN_SEG]
    if not spans:
        return {}
    answer = embed(wav, spans, url, key)
    out = {}
    for label, centroid in (answer.get("centroids") or {}).items():
        out[label] = {"centroid": centroid,
                      "air_sec": round(float((answer.get("air") or {}).get(label) or
                                             voices.get(label, {}).get("air_sec", 0)), 1),
                      "cluster": label}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="отпечатки голосов записи для узнавания на сервере")
    ap.add_argument("artifact", type=Path, help="артефакт адаптера (artifact.json / <id>.json)")
    ap.add_argument("audio", type=Path, help="звук записи (любой формат — приведём к wav 16k)")
    ap.add_argument("-o", "--out", type=Path, help="куда писать (умолчание — voices.json рядом)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--key", default="")
    args = ap.parse_args()
    out = args.out or args.artifact.parent / "voices.json"
    prints = fingerprints(args.artifact, args.audio, url=args.url, key=args.key)
    if not prints:
        print("отпечатков нет: в записи не нашлось безымянных голосов с речью длиннее двух секунд")
        return 1
    out.write_text(json.dumps(prints, ensure_ascii=False), encoding="utf-8")
    air = ", ".join(f"{label} {rec['air_sec']:.0f}с" for label, rec in prints.items())
    print(f"{out}: голосов {len(prints)} — {air}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
