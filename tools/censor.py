#!/usr/bin/env python3
"""Цензурные замены: что именно поменяется в корпусе — ДО того, как менять.

    python3 tools/censor.py --root бля      # разведка по корню, конфиг не нужен
    python3 tools/censor.py                 # по правилам из text_fixes.json
    python3 tools/censor.py --context 5     # больше примеров на форму

Чем цензура отличается от гарблов и почему живёт отдельным ключом `censor`, а не в `global`:

  * гарбл — это ошибка ASR («пост грез» вместо «Постгрес»); цензура — расслышано ВЕРНО, и это
    решение владельца не печатать. Ключ `_scope` словаря прямо запрещает обычные слова в
    `global`: глобальная замена обычного слова портит его в другом докладе;
  * правая часть каждого global-правила уезжает в подсказки КАЖДОЙ записи
    (`make_meta.corpus_canon`) как «верное написание» — конвейер начал бы получать «кхм»
    каноником и подгонять под него звук;
  * `leak_check.forbidden()` берёт обе стороны словаря в список запретных слов публичного
    морага — мат попал бы туда и стал генератором ложных тревог.

⚠️ Правило задаётся КОРНЕМ и ловит любую форму, поэтому смотреть глазами обязательно: корень
цепляет и законные слова с той же основой. Всё лишнее уезжает в `except` правила.

⚠️ Замена обязана быть ОДНИМ токеном без пробелов. Замерено: пробел в замене делает в
`record.words.json` «слово» с пробелом, после чего разрез на абзацы для этой реплики молча
отключается — и часовой доклад становится одной репликой с одним тайм-кодом.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaces  # noqa: E402

FIXES = spaces.family_dir() / "text_fixes.json"
# Хвост формы — только кириллица: цифры и латиница за корнем означают другое слово.
TAIL = r"[а-яё]*"


def rule_re(root: str) -> re.Pattern:
    """Регулярка правила: та же граница слова, что у гарблов, плюс любой хвост формы.

    `\\b` в питоне на кириллице ведёт себя как надо только с юникодными классами, поэтому
    границу задаём явными lookaround'ами — так же, как в `make_record.apply_text_fixes`.
    """
    return re.compile(rf"(?<![^\W\d_]){re.escape(root)}{TAIL}(?![^\W\d_])", re.IGNORECASE)


def load_rules() -> list[dict]:
    if not FIXES.is_file():
        return []
    data = json.loads(FIXES.read_text(encoding="utf-8"))
    return list((data.get("censor") or {}).get("rules") or [])


def bodies() -> list[tuple[str, str]]:
    """(id записи, тело расшифровки) по всем пространствам. Шапку не берём: её не правим."""
    out = []
    for root in spaces.records_dirs():
        for md in sorted(root.glob("**/record.md")):  # ⚠️ рекурсивно: записи лежат по веткам и годам, шаблон на один уровень даст ТИХИЙ ноль
            text = md.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            out.append((md.parent.name, parts[2] if len(parts) > 2 else text))
    return out


def survey(rules: list[dict], context: int) -> int:
    docs = bodies()
    if not docs:
        print("записей не нашлось — проверьте, что корпус на месте")
        return 1

    total_hit = 0
    for rule in rules:
        root, now = rule["root"], rule.get("now", "кхм")
        skip = {w.lower() for w in (rule.get("except") or [])}
        rx = rule_re(root)
        forms: Counter = Counter()
        per_record: Counter = Counter()
        samples: dict[str, list[str]] = defaultdict(list)
        spared: Counter = Counter()

        for rid, body in docs:
            for m in rx.finditer(body):
                word = m.group(0)
                if word.lower() in skip:
                    spared[word] += 1
                    continue
                forms[word] += 1
                per_record[rid] += 1
                if len(samples[word]) < context:
                    left = body[max(0, m.start() - 42):m.start()].replace("\n", " ")
                    right = body[m.end():m.end() + 42].replace("\n", " ")
                    samples[word].append(f"…{left}[{word}]{right}…")

        hits = sum(forms.values())
        total_hit += hits
        print(f"\n=== корень «{root}» → «{now}» ===")
        if " " in now:
            print("  ⚠️ В замене ПРОБЕЛ. Так нельзя: пословные времена получат «слово» с "
                  "пробелом, и разрез на абзацы молча отключится.")
        print(f"  вхождений {hits} в {len(per_record)} записях из {len(docs)}; форм {len(forms)}")
        if spared:
            print(f"  пропущено по `except`: {sum(spared.values())} "
                  f"({', '.join(f'{w}×{n}' for w, n in spared.most_common())})")
        if not hits:
            print("  ничего не нашлось")
            continue

        print(f"\n  {'форма':22s} {'сколько':>8s}")
        for word, n in forms.most_common():
            print(f"  {word:22s} {n:8d}")
        print("\n  примеры (смотреть глазами — корень ловит и законные слова):")
        for word, n in forms.most_common():
            for s in samples[word]:
                print(f"    {s}")
        print("\n  записи с наибольшим числом вхождений:")
        for rid, n in per_record.most_common(8):
            print(f"    {n:3d}  {rid}")
    return 0 if total_hit or not rules else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=[],
                    help="разведка по корню, минуя конфиг (можно несколько раз)")
    ap.add_argument("--now", default="кхм", help="чем заменять при разведке")
    ap.add_argument("--context", type=int, default=3, help="сколько примеров на форму")
    args = ap.parse_args()

    rules = [{"root": r, "now": args.now} for r in args.root] or load_rules()
    if not rules:
        print("правил нет: заведите `censor.rules` в text_fixes.json или укажите --root")
        return 1
    return survey(rules, args.context)


if __name__ == "__main__":
    raise SystemExit(main())
