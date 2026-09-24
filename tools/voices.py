#!/usr/bin/env python3
"""Снимок голосов корпуса — то, из чего верстак предлагает имена.

    python3 tools/voices.py --scan     # собрать voices.json в каталоге семьи корпуса
    python3 tools/voices.py            # показать сводку по уже собранному

Зачем файл, а не расчёт на лету: самопредставления лежат в СЫРЫХ сайдкарах (231 МБ на корпус),
и читать их на каждый запрос страницы нельзя. Плюс обратного индекса «реестр голосов → записи»
не существует вовсе: провенанс реестра у всех записей одинаковый, и связь восстанавливается
только обходом всех записей.

⚠️ Снимок ПРОИЗВОДНЫЙ и в git не едет: он собирается из сайдкаров, которых нет нигде, кроме
машины транскрибации. Нет файла — верстак честно скажет «сначала соберите снимок».

Что знает про голос:
  * сколько эфира и в каких записях (из `record.words.json`, он в git);
  * самопредставления — «меня зовут …» из СЫРОГО текста реплики;
  * кандидатов из меты тех записей, где голос звучит, с происхождением и числом голосов «за»;
  * что уже стоит в `names.json`.

⚠️ Самопредставление берём из `turns[].raw`, а не из готового текста. Причина записана в
`names.json._source`: финал-раунд умеет дочинить обрывок до правдоподобного имени, которого в
звуке не было. Один такой случай в корпусе уже есть.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def short(path: Path) -> str:
    """Путь для человека. ⚠️ Не `relative_to` от каталога платформы: корпус лежит ОТДЕЛЬНО от
    неё (так устроено разделение), и снимок собирался, а инструмент падал на последней строке —
    при печати имени только что записанного файла."""
    for base in (Path.cwd(), REPO, Path.home()):
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        return ("~/" + str(rel)) if base == Path.home() else str(rel)
    return str(path)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaces  # noqa: E402

OUT = spaces.family_dir() / "voices.json"
NAMES = spaces.family_dir() / "names.json"

# Как человек представляется. Список намеренно узкий: широкий ловит «меня зовут в отпуск»
# и тонет в шуме, а пропущенное самопредставление всегда добирается кандидатами из меты.
INTRO_RE = re.compile(r"(меня зовут|моя фамилия|represent|представлюсь|я\s+—\s)", re.I)
# Сколько символов показать вокруг находки: имя стоит сразу после, но должность и команда —
# следом, и они помогают отличить однофамильцев.
INTRO_LEFT, INTRO_RIGHT = 40, 190
MAX_INTROS = 6
# Имя сразу после «меня зовут». Нужно не чтобы подставить его автоматически (на это есть
# человек), а чтобы заметить РАСХОЖДЕНИЕ: если один голос называет себя двумя разными именами,
# реестр склеил двух людей, и общее имя подпишет обоих.
SELF_NAME = re.compile(r"(?:меня зовут|моя фамилия)\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)", re.I)
# Разовый голос из зала: одна запись и мало эфира. Именем такие НЕ подписываем
# (правило `names.json._unnamed`), но показать их надо — вдруг это докладчик одной встречи.
SOLO_SEC = 120


def same_stem(names: list[str]) -> list[str]:
    """Свернуть написания одного имени в одно. Возвращает список РАЗНЫХ имён.

    ⚠️ Не префикс, а общая ОСНОВА: «Кузнецова» и «Кузнец» расходятся на седьмой букве и префиксами
    друг друга не являются, хотя это одно имя, оборванное на слух. А «Кузнецова» и «Вова» общей
    основы не имеют вовсе — вот это и есть склейка двух людей.
    """
    out: list[str] = []
    for name in names:
        if any(_shares_stem(name, kept) for kept in out):
            continue
        out.append(name)
    return out


def _shares_stem(a: str, b: str) -> bool:
    common = 0
    for x, y in zip(a.lower(), b.lower()):
        if x != y:
            break
        common += 1
    # Четыре буквы — чтобы «Инга»/«Инна» не слиплись; доля от короткого — чтобы длинная
    # фамилия не притянула к себе чужое имя с тем же началом.
    return common >= 4 and common >= 0.6 * min(len(a), len(b))


def load_names() -> tuple[dict[str, str], dict[str, str]]:
    if not NAMES.is_file():
        return {}, {}
    data = json.loads(NAMES.read_text(encoding="utf-8"))
    return dict(data.get("speakers") or {}), dict(data.get("_why") or {})


def scan() -> dict:
    sec: Counter = Counter()
    turns_count: Counter = Counter()
    in_records: dict[str, set[str]] = defaultdict(set)
    in_spaces: dict[str, set[str]] = defaultdict(set)
    record_space: dict[str, str] = {}
    meta_names: dict[str, list[dict]] = {}
    intros: dict[str, list[dict]] = defaultdict(list)

    for root in spaces.records_dirs():
        slug = root.parent.name
        # ⚠️ Рекурсивно: записи лежат по веткам и годам. Обход на один уровень отдавал бы ветки,
        # снимок голосов собрался бы ПУСТЫМ и не упал — а по нему правят имена всего корпуса.
        for rec in sorted(hit.parent for hit in root.glob("**/record.words.json")) if root.is_dir() else []:
            rid = rec.name
            record_space[rid] = slug

            words = rec / "record.words.json"
            if words.is_file():
                data = json.loads(words.read_text(encoding="utf-8"))
                for turn in data.get("turns") or []:
                    dur = float(turn.get("end", 0)) - float(turn.get("start", 0))
                    # ⚠️ Ключ — `speaker_id`, идентификатор РЕЕСТРА, а не видимая метка. По метке
                    # названный голос попадал в снимок под ИМЕНЕМ, и найти его по `Speaker_N`
                    # было нельзя: верстак открывался пустым, а правка имени — недоступной.
                    # Ловилось на `Speaker_0` (08.09). Запасной путь — для старых файлов слов,
                    # где поля ещё нет; там метка и есть идентификатор.
                    who = str(turn.get("speaker_id") or turn.get("speaker") or "")
                    if not who or dur <= 0:
                        continue
                    sec[who] += dur
                    turns_count[who] += 1
                    in_records[who].add(rid)
                    in_spaces[who].add(slug)

            meta = rec / "record.meta.json"
            if meta.is_file():
                m = json.loads(meta.read_text(encoding="utf-8"))
                meta_names[rid] = [s for s in (m.get("speakers") or []) if s.get("name")]

            # Сайдкар — единственный источник СЫРОГО текста. Он же самый тяжёлый, поэтому
            # читаем один раз и только ради самопредставлений.
            side = rec / "record.json"
            if not side.is_file():
                continue
            x = json.loads(side.read_text(encoding="utf-8")).get("x_enriched") or {}
            for turn in x.get("turns") or []:
                who = str(turn.get("speaker_id") or turn.get("speaker") or "")
                raw = str(turn.get("raw") or turn.get("text") or "")
                if not who or len(intros[who]) >= MAX_INTROS:
                    continue
                m = INTRO_RE.search(raw)
                if not m:
                    continue
                intros[who].append({
                    "record": rid,
                    "sec": round(float(turn.get("start") or 0), 1),
                    "raw": raw[max(0, m.start() - INTRO_LEFT):m.start() + INTRO_RIGHT].strip(),
                })

    named, why = load_names()
    voices = []
    for who, total in sec.most_common():
        recs = sorted(in_records[who])
        votes: Counter = Counter()
        origin: dict[str, str] = {}
        for rid in recs:
            for cand in meta_names.get(rid, []):
                votes[cand["name"]] += 1
                origin.setdefault(cand["name"], cand.get("from", "?"))
        # Склейка голосов: одно и то же «меня зовут» с разными именами. Сравниваем по первому
        # слову — порядок «Имя Фамилия»/«Фамилия Имя» и падежи иначе дали бы ложную тревогу.
        said = []
        for intro in intros.get(who, []):
            m = SELF_NAME.search(intro["raw"])
            if m:
                said.append(m.group(1).strip())
        distinct = same_stem(sorted({s.split()[0] for s in said}))

        voices.append({
            "id": who,
            # ⚠️ Не приговор, а повод послушать: расшифровка могла и переврать имя.
            "conflict": distinct if len(distinct) > 1 else [],
            "sec": round(total, 1),
            "turns": turns_count[who],
            "records": recs,
            "spaces": sorted(in_spaces[who]),
            "name": named.get(who, ""),
            "why": why.get(who, ""),
            # Разовый голос из зала — подсказка верстаку, а не запрет.
            "solo": len(recs) == 1 and total < SOLO_SEC,
            "intros": intros.get(who, []),
            "candidates": [{"name": n, "votes": v, "from": origin[n]}
                           for n, v in votes.most_common()],
        })

    vocabulary = sorted({c["name"] for names in meta_names.values() for c in names}
                        | set(named.values()))
    return {
        "_doc": "Снимок голосов корпуса для верстака имён. Производный файл, в git не едет: "
                "собирается из сырых сайдкаров, которых нет вне машины транскрибации.",
        "_built_from": {"records": len(record_space), "voices": len(voices)},
        "vocabulary": vocabulary,
        # Запись → пространство: адрес читалки идёт с префиксом раздела, а голос общий на весь
        # корпус и своего пространства не имеет. Без этой карты ссылка из верстака была бы битой.
        "record_space": record_space,
        "voices": voices,
    }


def summary(data: dict) -> None:
    voices = data["voices"]
    total = sum(v["sec"] for v in voices) or 1
    named = [v for v in voices if v["name"]]
    solo = [v for v in voices if v["solo"]]
    print(f"голосов {len(voices)}, речи {total / 3600:.1f} ч, названы {len(named)}")
    print(f"разовых (одна запись, меньше {SOLO_SEC // 60} мин): {len(solo)} — их не подписываем")
    clash = [v for v in voices if v.get("conflict")]
    if clash:
        print(f"⚠️ называют себя РАЗНЫМИ именами: {len(clash)} — похоже на склейку в реестре:")
        for v in sorted(clash, key=lambda v: -v["sec"]):
            print(f"     {v['id']:13s} {v['sec'] / 3600:5.1f} ч в {len(v['records'])} записях: "
                  f"{', '.join(v['conflict'])}")
    print(f"словарь имён корпуса: {len(data['vocabulary'])}\n")
    print(f"{'голос':14s} {'часов':>6s} {'зап':>4s} {'предст':>7s} {'канд':>5s}  имя / подсказка")
    run = 0.0
    for v in voices[:30]:
        run += v["sec"]
        hint = v["name"] or (v["candidates"][0]["name"] if v["candidates"] else "")
        mark = "✓" if v["name"] else (" " if hint else " ")
        print(f"{v['id']:14s} {v['sec'] / 3600:6.1f} {len(v['records']):4d} "
              f"{len(v['intros']):7d} {len(v['candidates']):5d}  {mark} {hint}")
    print(f"\nтоп-30 = {run / total * 100:.0f}% эфира")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true", help="пересобрать снимок (читает сайдкары)")
    args = ap.parse_args()

    if args.scan:
        data = scan()
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"снимок собран: {short(OUT)}\n")
    elif OUT.is_file():
        data = json.loads(OUT.read_text(encoding="utf-8"))
    else:
        print(f"нет {short(OUT)} — соберите: python3 tools/voices.py --scan")
        return 1
    summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
