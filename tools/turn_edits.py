#!/usr/bin/env python3
"""Правка реплики с сайта: хранение, применение и пересчёт пословных времён.

Словарь `turn_fixes.json` — вторая, ПОМЕСТНАЯ форма правки. `text_fixes.json` умеет только
«написание → написание», один токен в один: удалить слово им нельзя (менялось бы число слов),
вставить нечем, а заменить он умеет только ВЕЗДЕ сразу. Здесь правится КУСОК КОНКРЕТНОЙ РЕПЛИКИ,
и три операции — замена, удаление, вставка — выводятся из разницы «было/стало», а не задаются
отдельными командами.

Правка адресуется СОДЕРЖИМЫМ, а не номером слова:

    {"turn": 12, "at": 40, "was": "Мы используем Постгрез для", "now": "Мы используем Postgres для"}

`was` ищется как подряд идущие слова внутри реплики сырца. Не нашлось — правка НЕ применяется и
громко об этом говорит. Это и предохранитель (запись перетранскрибировали — границы реплик поехали),
и то, что делает словарь читаемым глазами: видно, что именно поправили, без похода в запись.
`at` — только подсказка для развязки, если такой кусок в реплике встречается дважды.

⚠️ Тайм-код в роли якоря не годится: замерено 08.09 на правке правила разреза — из 2187 меток
реплик пересборку пережили 1323, 60%. Привязанная к метке правка уехала бы в чужое место.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path

# Слово в `morag-words-v1` — это `[текст, начало, конец]`. Мы приписываем ЧЕТВЁРТЫМ элементом
# адрес `[номер реплики, номер слова]` и прогоняем сборку на копии — так узнаём, из какого места
# сырца взялся абзац, который человек видит в читалке.
#
# ⚠️ Держится это на трёх местах в `make_record.py`, и все три проверены: `substitute` собирает
# слово как `[rx.sub(w[0]), *w[1:]]` (хвост цел), `drop_degenerate` выбрасывает запись целиком,
# `paragraphs_of` и `split_long_turns` читают только первые три элемента и перекладывают слово как
# есть. Тест `test_turn_edits.py` сверяет прогон с настоящим `record.words.json` — если порядок
# сборки разойдётся, это увидят там, а не в испорченном тексте.
ADDR = 3


class EditError(Exception):
    """Правку применить нельзя. Текст сообщения уходит и в лог сборки, и в ответ сайта."""


# --- чтение словаря ---------------------------------------------------------

def load(corpus: Path, record_id: str) -> list[dict]:
    """Правки одной записи. Нет файла — пустой список: словарь необязателен."""
    path = corpus / "turn_fixes.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list((data.get("records") or {}).get(record_id) or [])


# --- пересчёт времён --------------------------------------------------------

def _spread(texts: list[str], lo: float, hi: float) -> list[list]:
    """Разложить слова по промежутку `lo…hi` пропорционально длине.

    Промежуток пустой (вставили туда, где паузы нет) — все слова получают одно и то же время
    нулевой ширины. Это не красиво, но ЧЕСТНО и главное — не ломает порядок: караоке ищет слово
    двоичным поиском и на убывающем времени врёт молча. Двигать соседние, измеренные слова ради
    красоты нельзя: их время настоящее, а вставленного в звуке нет вовсе.
    """
    if not texts:
        return []
    span = max(0.0, hi - lo)
    total = sum(len(t) for t in texts) or len(texts)
    out, cursor = [], lo
    for text in texts:
        width = span * (len(text) / total)
        out.append([text, round(cursor, 2), round(cursor + width, 2)])
        cursor += width
    return out


def retime(old: list[list], now: list[str], bounds: tuple[float, float]) -> list[list]:
    """Слова куска после правки: что не тронули — с ТЕМ ЖЕ временем, байт в байт.

    Сравниваем списки слов, а не строки: правка приходит текстом, но времена привязаны к словам,
    и «переписали реплику целиком» здесь распадается на «эти слова остались, это ушло, это
    появилось». Делится только промежуток изменённого куска.

    `bounds` — время, свободное вокруг куска (конец предыдущего слова реплики и начало
    следующего). Без него вставка на самом краю осталась бы без места.
    """
    sm = SequenceMatcher(a=[w[0] for w in old], b=now, autojunk=False)
    out: list[list] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend([list(w) for w in old[i1:i2]])
        elif tag == "delete":
            continue  # слово исчезает, его время остаётся дырой — честной, как все прочие
        elif tag == "replace":
            # Новые слова делят промежуток тех, кого заменили.
            out.extend(_spread(now[j1:j2], old[i1][1], old[i2 - 1][2]))
        else:  # insert — берём паузу между соседями
            lo = old[i1 - 1][2] if i1 > 0 else bounds[0]
            hi = old[i1][1] if i1 < len(old) else bounds[1]
            out.extend(_spread(now[j1:j2], lo, max(lo, hi)))
    return out


# --- применение -------------------------------------------------------------

def _find(words: list[str], was: list[str], at: int | None) -> int:
    """Где в реплике лежит кусок `was`. Несколько совпадений — берём ближайшее к подсказке."""
    hits = [i for i in range(len(words) - len(was) + 1) if words[i:i + len(was)] == was]
    if not hits:
        raise EditError(f"кусок {' '.join(was)[:60]!r} в реплике не найден")
    if len(hits) == 1 or at is None:
        return hits[0]
    return min(hits, key=lambda i: abs(i - at))


def apply(x: dict, edits: list[dict], warn=print) -> list[dict]:
    """Применить правки реплик к артефакту: тело, реплики и пословные времена разом.

    ⚠️ Три места обязаны остаться согласованными. `split_long_turns` сверяет
    `[w[0] for w in words]` с `text.split()` и при расхождении МОЛЧА перестаёт резать реплику на
    абзацы — часовой доклад становится одной простынёй с одним тайм-кодом.
    """
    from make_record import UTTERANCE_RE, split_head  # цикл импорта не нужен, зовём по месту

    if not edits:
        return []
    wt = ((x.get("words") or {}).get("turns")) or []
    turns = x.get("turns") or []
    head, body = split_head(x.get("markdown") or "")
    lines = [ln for ln in body.split("\n\n") if ln.strip()]
    if not wt or len(lines) != len(wt):
        warn("⚠️ правки реплик не применены: расшифровка и пословные времена разошлись")
        return []

    applied = []
    for edit in edits:
        t = edit.get("turn")
        was, now = str(edit.get("was", "")).split(), str(edit.get("now", "")).split()
        if not isinstance(t, int) or not 0 <= t < len(wt):
            warn(f"⚠️ правка реплики: нет реплики {t} — пропущена")
            continue
        if not now:
            warn(f"⚠️ правка реплики {t}: пустая замена — пропущена")
            continue
        words = wt[t]["words"]
        try:
            i = _find([w[0] for w in words], was, edit.get("at"))
        except EditError as e:
            warn(f"⚠️ правка реплики {t} не применена: {e} — запись меняли, правку надо перечитать")
            continue
        j = i + len(was)
        lo = words[i - 1][2] if i > 0 else wt[t].get("start", words[0][1])
        hi = words[j][1] if j < len(words) else wt[t].get("end", words[-1][2])
        fresh = retime(words[i:j], now, (float(lo), float(hi)))
        wt[t]["words"] = words[:i] + fresh + words[j:]
        # Границы реплики следуют за словами: правка могла задеть первое или последнее.
        if wt[t]["words"]:
            wt[t]["start"] = round(float(wt[t]["words"][0][1]), 2)
            wt[t]["end"] = round(float(wt[t]["words"][-1][2]), 2)
        text = " ".join(w[0] for w in wt[t]["words"])
        if t < len(turns):
            # `raw` не трогаем: это то, что расслышал ASR, и по нему потом ищут самопредставления.
            turns[t]["text"] = text
        m = UTTERANCE_RE.match(lines[t])
        lines[t] = (lines[t][:m.end()] if m else "") + text
        applied.append({"turn": t, "was": " ".join(was), "now": " ".join(now)})

    new_body = "\n\n".join(lines) + "\n"
    x["markdown"] = f"{head}\n\n{new_body}" if head else new_body
    if applied:
        x["turn_edits"] = applied
    return applied


# --- обратный путь: абзац читалки → кусок реплики сырца ----------------------

def stamp(x: dict) -> None:
    """Приписать каждому слову его адрес в реплике. Зовётся ПОСЛЕ применения правок реплик:
    вставленные слова тоже обязаны получить адрес, иначе следующая правка того же места
    не найдёт себе координат."""
    for t, turn in enumerate(((x.get("words") or {}).get("turns")) or []):
        turn["words"] = [[*list(w)[:3], [t, i]] for i, w in enumerate(turn.get("words") or [])]


def slices(x: dict) -> list[dict | None]:
    """Для каждого абзаца готовой расшифровки — кусок реплики сырца, из которого он вышел.

    ⚠️ «Номер абзаца плюс смещение» до сырца НЕ доводит: до разреза на абзацы отрабатывает
    `drop_degenerate`, а он УДАЛЯЕТ слова (1758 слов в 25 записях курса QA). Поэтому адрес
    каждого слова несём с собой, а границы куска берём по краям.
    """
    out: list[dict | None] = []
    for turn in ((x.get("words") or {}).get("turns")) or []:
        addrs = [w[ADDR] for w in turn.get("words") or [] if len(w) > ADDR and w[ADDR]]
        if not addrs:
            out.append(None)
            continue
        t = addrs[0][0]
        idx = [a[1] for a in addrs if a[0] == t]
        out.append({"turn": t, "from": min(idx), "to": max(idx)})
    return out


def unstamp(x: dict) -> None:
    """Снять адреса — слово обязано уехать на диск тройкой, как велит `morag-words-v1`."""
    for turn in ((x.get("words") or {}).get("turns")) or []:
        turn["words"] = [list(w)[:3] for w in turn.get("words") or []]


def trace(record_dir: Path) -> tuple[dict, list[dict | None], list[list[str]]]:
    """Прогнать сборку записи на копии. Отдаёт три вещи: готовый артефакт (то, что человек видит
    в читалке), срезы абзацев в координатах реплик и сами реплики СЛОВАМИ СЫРЦА.

    Третье — не удобство, а необходимость: правка хранится в словаре текстом сырца («что было»),
    а человек видит текст ПОСЛЕ гарблов и цензуры. Без снимка сырца новая правка записалась бы
    тем, чего в сырце нет, и при сборке не нашлась бы.

    ⚠️ Порядок обязан совпадать с `build_one` в `make_record.py` слово в слово: разойдись он —
    и абзац в читалке отвечал бы не тому куску сырца. Стеречь это глазами бессмысленно, поэтому
    тест `test_turn_edits.py` сверяет полученные абзацы с настоящим `record.words.json` записи.
    """
    import spaces
    from make_record import (apply_censor, apply_overrides, apply_text_fixes, drop_degenerate,
                             load_dicts, split_long_turns)

    artifact = record_dir / "record.json"
    # ⚠️ Сырец лежит ВНЕ git и приезжает только с машины транскрибации: запись, собранная в
    # другом чекауте, на сайте оказывается без него (ловилось 14.09 на двух новых записях —
    # правка отвечала 500). Отсюда понятный отказ, а не FileNotFoundError.
    if not artifact.is_file():
        raise EditError(f"{record_dir.name}: сырого сайдкара record.json нет — править нечего "
                        "(сырец вне git, скопировать с машины транскрибации)")
    x = json.loads(artifact.read_text(encoding="utf-8")).get("x_enriched") or {}
    if not x.get("markdown"):
        raise EditError(f"{record_dir.name}: сырого сайдкара нет — править нечего")
    corpus = spaces.corpus_root(record_dir)
    record_id = record_dir.name
    names, fixes, censor = load_dicts(corpus, record_id)
    apply_overrides(x, names)
    apply(x, load(corpus, record_id), warn=lambda m: None)
    stamp(x)  # адрес приписываем ПОСЛЕ правок: вставленные слова тоже адресуемы
    base = [[w[0] for w in (t.get("words") or [])]
            for t in ((x.get("words") or {}).get("turns")) or []]
    apply_text_fixes(x, fixes)
    apply_censor(x, censor)
    drop_degenerate(x)
    split_long_turns(x)
    return x, slices(x), base
