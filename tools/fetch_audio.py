#!/usr/bin/env python3
"""Звук записей корпуса с сервера — 16 кГц моно flac в ~/asr-stack/audio/<id>.flac, тем же способом,
что звук берётся для транскрибации: ffmpeg работает НА СЕРВЕРЕ (вынимает дорожку из видео в
архиве), сюда едет только flac (~60 МБ на час против ~450 МБ видео).

Зачем (13.09): стек транскрибации и реестр голосов с ноутбука исчезли; реестр пересобирается по
звуку и `speaker_map` сайдкаров. `video_batch.py` сохраняет звук сам, а этот инструмент добирает
записи, пройденные до того, как он начал это делать, и вообще любые по списку.

  python3 <morag-web>/tools/fetch_audio.py --missing          # всем записям с видео, у которых flac ещё нет
  python3 tools/fetch_audio.py --missing --done   # только тем, что уже прошли конвейер экрана
  python3 tools/fetch_audio.py --id <id> [--id …] # по списку
"""
from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

# ⚠️ Шима соседнего репозитория здесь БОЛЬШЕ НЕТ: инструмент переехал в morag-web и лежит рядом
# с тем, что импортирует. Оставшийся `import _web` делал его незапускаемым иначе как из каталога
# корпуса (`ModuleNotFoundError: _web`) — а докстрока звала запускать как обычный скрипт.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_slides_md import read_header  # noqa: E402
from video_batch import HOST, server_path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
AUDIO_DIR = Path.home() / "asr-stack" / "audio"
REMOTE_DIR = "/tmp/morag-audio-corpus"


def plan(root: Path, ids: list[str], missing: bool, done_only: bool) -> list[tuple[str, str]]:
    out = []
    for md in sorted(root.glob("**/record.md")):
        rec = md.parent
        media = read_header(md).get("media", "").strip().strip('"')
        if not media:
            continue
        if ids and rec.name not in ids:
            continue
        if missing and (AUDIO_DIR / f"{rec.name}.flac").is_file():
            continue
        if done_only and not (rec / "record.annotations.json").is_file():
            continue
        if ids or missing:
            out.append((rec.name, media))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=None, help="каталог записей (по умолчанию — каталог записей первого пространства корпуса)")
    ap.add_argument("--id", action="append", default=[])
    ap.add_argument("--missing", action="store_true")
    ap.add_argument("--done", action="store_true", help="с --missing: только записи, прошедшие конвейер экрана")
    ap.add_argument("--chunk", type=int, default=10, help="записей на одну порцию (≤ ~1 ГБ на /tmp сервера)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.root is None:
        import spaces  # noqa: PLC0415
        a.root = spaces.records_dirs()[0]
    todo = plan(a.root, a.id, a.missing, a.done)
    if not todo:
        print("нечего забирать"); return 0
    for rid, media in todo:
        print(f"  {rid}  ←  {media}")
    if a.dry:
        print(f"итого {len(todo)}"); return 0
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    # ⚠️ Порциями: flac лежит на /tmp сервера — это может быть КОРНЕВОЙ раздел (13.09 другой инструмент
    # положил туда 4.6 ГБ и оставил 0 свободных). Порция в 10 записей — ≤ ~1 ГБ, забирается и стирается
    # до следующей. ~10 с ffmpeg + ~30 с rsync на часовую запись.
    for i in range(0, len(todo), max(1, a.chunk)):
        part = todo[i:i + max(1, a.chunk)]
        # ⚠️ Путь на сервере — через base64: пробелы и кириллица в именах файлов, а строка идёт через stdin
        # удалённой оболочки.
        lines = "".join(f"{base64.b64encode(server_path(m).encode()).decode()} {rid}\n" for rid, m in part)
        script = (
            f"mkdir -p {REMOTE_DIR} && while read -r b id; do p=$(echo \"$b\" | base64 -d); out=\"{REMOTE_DIR}/$id.flac\"; "
            "if nice -n 19 ffmpeg -hide_banner -loglevel error -y -i \"$p\" -vn -ar 16000 -ac 1 -c:a flac \"$out\" </dev/null; "
            "then echo \"готово: $id\"; else echo \"СБОЙ: $id\"; fi; done"
        )
        print(f"[{i + 1}-{i + len(part)} из {len(todo)}] вынимаю звук на сервере…", flush=True)
        res = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, script], input=lines, text=True, capture_output=True)
        print(res.stdout.strip(), flush=True)
        if res.returncode:
            print(res.stderr.strip()[-500:], file=sys.stderr)
        pull = subprocess.run(["rsync", "-a", f"{HOST}:{REMOTE_DIR}/", str(AUDIO_DIR) + "/"])
        if pull.returncode:
            print("rsync не отработал — файлы остались на сервере, повторите запуск", file=sys.stderr)
            return 1
        subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, f"rm -rf {REMOTE_DIR}"])
    got = sum(1 for rid, _ in todo if (AUDIO_DIR / f"{rid}.flac").is_file())
    print(f"в {AUDIO_DIR}: {got} из {len(todo)}")
    return 0 if got == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())
