"""Где расшифровка потеряла речь: дыры между выровненными словами.

После перехода на глобальное выравнивание пропуск ASR больше не «съедается»
сдвигом соседей — он остаётся видимой дырой во времени. Значит по `.words.json`
можно посчитать, сколько речи не попало в текст, и найти конкретные места.

    python3 tools/find_gaps.py corpora/<семья> [--min 10]

Дыра ВНУТРИ реплики — почти наверняка потерянный кусок: между репликами паузы
нормальны, а внутри одной реплики человек не молчит по полминуты.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="дыры в расшифровках по пословным тайм-кодам")
    ap.add_argument("target", help="каталог корпуса (corpora/<имя>)")
    ap.add_argument("--min", type=float, default=10.0, help="с какой длины считать дырой, секунд")
    args = ap.parse_args()

    # `**`: записи лежат по пространствам. Прежний шаблон на новой раскладке не находил
    # НИЧЕГО и честно сообщал «записи ещё не собраны» — тихий ноль вместо ошибки.
    files = sorted(Path(args.target).glob("**/records/**/record.words.json"))  # ⚠️ рекурсивно: записи лежат по веткам и годам, шаблон на один уровень даст ТИХИЙ ноль
    if not files:
        print("нет пословных тайм-кодов: записи ещё не собраны (tools/make_record.py)")
        return

    total_lost = 0.0
    total_audio = 0.0
    rows = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        total_audio += float(data.get("duration_sec") or 0)
        lost = 0.0
        worst = []
        for turn in data["turns"]:
            words = [w for w in turn["words"] if w[2] > w[1]]
            for a, b in zip(words, words[1:]):
                gap = b[1] - a[2]
                if gap >= args.min:
                    lost += gap
                    worst.append((gap, a[2], a[0], b[0]))
        if worst:
            worst.sort(reverse=True)
            total_lost += lost
            rows.append((lost, path.parent.name, len(worst), worst[0]))

    rows.sort(reverse=True)
    print(f"записей с дырами: {len(rows)} из {len(files)}")
    print(f"потеряно речи: {total_lost / 60:.1f} мин из {total_audio / 3600:.1f} ч ({total_lost / total_audio * 100:.2f}%)\n")
    for lost, name, count, (gap, at, before, after) in rows[:12]:
        mm, ss = int(at) // 60, int(at) % 60
        print(f"  {name:<28} потеряно {lost / 60:4.1f} мин в {count:2} местах · "
              f"худшее {gap:5.1f}с на {mm}:{ss:02d} («{before} … {after}»)")


if __name__ == "__main__":
    main()
