#!/usr/bin/env python3
"""Приёмка партии: что получилось у конвейера, одной таблицей.

    python3 tools/batch_report.py                    # по свежим расшифровкам в inbox/
    python3 tools/batch_report.py corpora/<семья>/records   # по собранным записям

Смысл — увидеть провал ДО того, как запись уедет в корпус. Проверяем то, на чём уже обжигались:

  ⚠️ **один голос на всю запись** — но причины ДВЕ, и путать их нельзя. Либо реестр схлопнул
     зал в докладчика (так было на credit_limit, пока не нашли константу `MIN_GUEST_MIN`), либо
     столько услышал сам диаризатор. Второе на коротком демо — норма: в корпусе четыре такие
     записи, три из них по 5-7 минут, и это правда монологи;
     ⓘ А «кластеров много, голос один» — вообще НЕ обязательно дефект: диаризатор часто дробит
     одного человека, и свести его обратно — прямая работа реестра. Числа этих случаев не
     различают, поэтому инструмент просит послушать, а не выносит приговор;
  ⚠️ **потери речи** — сколько звука не попало в текст. На корпусе митапов норма около 1%,
     всё, что заметно больше, — повод посмотреть глазами. ⓘ Но сперва на ТИШИНУ: у «Hadoop»
     набралось 29%, и все они пришлись на 34 минуты цифрового молчания (−91 дБ) — запись
     продолжала идти после встречи. Такое у пяти записей из 73;
  ⚠️ **вырожденные реплики** — whisper на тишине не молчит, а сочиняет: «Извание Извание
     Извание», «не знаю» 70 раз подряд. Ловим по повтору фразы подряд. Первопричину режет
     `tools/trim_tail.py` на входе, но тишина бывает и в середине записи;
  ⚠️ **пустой глоссарий** — стадия не нашла ни одного термина, хотя речь техническая;
  ⚠️ **нет пословных времён** — караоке для этой записи не заработает;
  ⚠️ **скорость** — насколько прогон отстал от привычных 6-7× реального времени.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT = None  # по умолчанию — inbox/ семьи корпуса (spaces.family_dir())
LOST_ALARM = 3.0     # % потерянной речи, выше которого смотрим глазами
SLOW_ALARM = 4.0     # ×реального времени, ниже которого прогон подозрительно медленный
# ⚠️ Вырождение ловим по ПОВТОРУ ФРАЗЫ ПОДРЯД, а не по доле уникальных слов. Доля — ловушка:
# она падает с длиной реплики сама по себе, и на сыром артефакте (реплики до 57 минут, пока их не
# разрезал make_record) честная речь даёт 0.27 при 7868 словах и 2130 уникальных. Порог 0.3 объявил
# мусором треть корпуса; настоящего мусора там 1811 слов в четырёх записях из 63.
# Повтор же длиной от четырёх — подпись деген-петли whisper и от длины реплики не зависит.
DEGEN_RUN = re.compile(r"\b([\w'-]{2,}(?:\s+[\w'-]{2,}){0,3})(?:[,.!?…]?\s+\1\b){3,}", re.I | re.U)
DEGEN_ALARM = 40     # слов повтора на запись, ниже которого это обычная человеческая речь


def stack_line(env: dict) -> str:
    """Отпечаток одной строкой: машина, коммит движка, версии решающих пакетов, модель LLM.

    ⚠️ Разные отпечатки внутри ОДНОЙ партии — это разъехавшиеся установки, и сравнивать такие
    записи между собой нельзя: другой частотник даёт другой глоссарий на том же тексте. Прогон на
    второй машине ради скорости — законный ход, но знать об этом надо ДО того, как начнёшь
    объяснять разницу в качестве свойствами записей.
    """
    if not env:
        return ""
    pkgs = " ".join(f"{k}{v}" for k, v in sorted((env.get("packages") or {}).items()))
    return " · ".join(x for x in (env.get("host"), env.get("morag"), pkgs, env.get("llm")) if x)


def artifacts(target: Path) -> list[Path]:
    if target.is_dir() and (target / "record.json").is_file():
        return [target / "record.json"]
    return sorted([*target.glob("*.json"), *target.glob("**/record.json")])  # ⚠️ рекурсивно: записи лежат по веткам и годам, шаблон на один уровень даст ТИХИЙ ноль


def row(path: Path) -> dict | None:
    try:
        x = (json.loads(path.read_text(encoding="utf-8")) or {}).get("x_enriched")
    except (OSError, ValueError):
        return None
    if not x:
        return None
    cov, timing = x.get("coverage") or {}, x.get("timing") or {}
    degen = 0
    for turn in x.get("turns") or []:
        for m in DEGEN_RUN.finditer(turn.get("final") or turn.get("raw") or ""):
            degen += len(re.findall(r"\w+", m.group(0), re.U))
    words = x.get("words") or {}
    audio = float(cov.get("audio_sec") or 0)
    total = float(timing.get("total_s") or 0)
    lost = float(cov.get("lost_sec") or 0)
    return {
        "id": path.parent.name if path.name == "record.json" else path.stem,
        "audio": audio,
        "total": total,
        "rt": audio / total if total else 0,
        "speakers": len(set((x.get("speaker_map") or {}).values())),
        "clusters": len(x.get("speaker_map") or {}),   # сколько нашёл диаризатор ДО реестра
        "turns": len(x.get("turns") or []),
        "gloss": int(timing.get("n_glossary") or 0),
        # Работа канала знания о записи: сколько верных написаний подтвердилось на черновике и
        # скольким кускам они реально достались. Ноль при непустой мете — канал не сработал.
        "hints": int(timing.get("n_hints") or 0),
        "hinted": int(timing.get("n_chunks_hinted") or 0),
        "lost": 100 * lost / audio if audio else 0,
        "words": int(words.get("words_total") or 0),
        "reordered": int(words.get("reordered") or 0),
        "degen": degen,
        # Отпечаток установки из артефакта (`x_enriched.env`, пишет fingerprint.py движка).
        # У записей, сделанных до 06.09, его нет — это не тревога, а просто «неизвестно».
        "env": stack_line(x.get("env") or {}),
    }


def main() -> int:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import spaces  # noqa: PLC0415
        target = spaces.family_dir() / "inbox"
    rows = [r for r in (row(p) for p in artifacts(target)) if r]
    if not rows:
        print(f"в {target} нет расшифровок")
        return 0

    print(f"{'запись':<46}{'мин':>5}{'обраб':>7}{'×RT':>6}{'голосов':>8}{'реплик':>7}"
          f"{'глосс':>6}{'подск':>6}{'потери':>8}{'слов':>7}")
    stacks = {r["env"] for r in rows if r["env"]}
    alarms = []
    if len(stacks) > 1:
        alarms.append(f"в партии {len(stacks)} РАЗНЫХ установки — записи несравнимы между собой:")
        alarms += [f"    · {st}" for st in sorted(stacks)]
    for r in rows:
        print(f"{r['id'][:46]:<46}{r['audio'] / 60:>5.0f}{r['total'] / 60:>6.0f}м{r['rt']:>6.1f}"
              f"{r['speakers']:>8}{r['turns']:>7}{r['gloss']:>6}"
              f"{(str(r['hints']) + '/' + str(r['hinted'])) if r['hints'] else '—':>6}"
              f"{r['lost']:>7.2f}%{r['words']:>7}")
        # ⚠️ Числа НЕ различают два случая, и прежняя формулировка врала, объявляя дефектом оба.
        # «Кластеров больше, голос один» бывает и когда реестр ошибочно слил двух людей, и когда
        # диаризатор разделил ОДНОГО на несколько, а реестр верно свёл обратно — то есть сделал
        # ровно то, ради чего заведён. Отличить может только ухо.
        # Замерено: за 167 записей это сработало ОДИН раз (Основы PostgreSQL, 6.5 мин), владелец
        # послушал — голос один, реестр прав. Поэтому не приговор, а просьба проверить.
        if r["speakers"] <= 1 < r["clusters"]:
            alarms.append(f"{r['id']}: диаризатор нашёл {r['clusters']} голосов, реестр свёл в ОДИН"
                          f" — послушать: один это человек или реестр слил двоих")
        elif r["speakers"] <= 1 and r["audio"] > 20 * 60:
            alarms.append(f"{r['id']}: диаризатор услышал ОДИН голос на {r['audio']/60:.0f} минут — "
                          f"проверить, вправду ли это монолог")
        if r["lost"] > LOST_ALARM:
            alarms.append(f"{r['id']}: потеряно {r['lost']:.1f}% речи — сперва проверить, "
                          f"не тишина ли это в хвосте записи")
        if r["degen"] >= DEGEN_ALARM:
            alarms.append(f"{r['id']}: {r['degen']} слов вырожденного текста — похоже, whisper "
                          f"сочинял на тишине; проверить tools/trim_tail.py на исходнике")
        if not r["gloss"]:
            alarms.append(f"{r['id']}: глоссарий пуст")
        if not r["words"]:
            alarms.append(f"{r['id']}: нет пословных времён — караоке не заработает")
        if r["rt"] and r["rt"] < SLOW_ALARM:
            alarms.append(f"{r['id']}: {r['rt']:.1f}× реального времени — медленнее обычного")
        if r["reordered"]:
            alarms.append(f"{r['id']}: порядок слов правился {r['reordered']} раз")

    audio = sum(r["audio"] for r in rows) / 3600
    work = sum(r["total"] for r in rows) / 3600
    print(f"\nитого {len(rows)} записей: {audio:.1f} ч звука за {work:.1f} ч работы "
          f"({audio / work if work else 0:.1f}× реального времени), "
          f"слов {sum(r['words'] for r in rows)}")
    if alarms:
        print("\n⚠️ на что посмотреть:")
        for a in alarms:
            print("  •", a)
    else:
        print("\n✅ ничего подозрительного")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
