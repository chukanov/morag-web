#!/usr/bin/env python3
"""Артефакт транскрибации → каталог записи корпуса.

    python3 tools/make_record.py corpora/<семья>/inbox/"Планёрка 12 марта.json" \
        --id 2026-03-12-kafka --title "Kafka без боли" --event "Backend-митап #17"

    python3 tools/make_record.py corpora/<семья>/records/2026-03-12-kafka   # пересобрать

Первый вызов собирает запись из свежего артефакта, второй — пересобирает уже готовую из её
сайдкара `record.json`. Пересборка нужна после пополнения `names.json`/`text_fixes.json`:
класть правку прямо в `record.md` НЕЛЬЗЯ — следующая пересборка её молча откатит.

Что делает:
  * применяет `names.json` (Speaker_N → имя) и `text_fixes.json` (гарблы) согласованно к телу,
    репликам и пословным тайм-кодам — один-в-один по токенам, поэтому тайминги не двигаются
    и перевыравнивание не нужно;
  * собирает шапку САМ. У артефакта она вырожденная: клиент знает только имя файла, поэтому
    `title` там — имя файла, `url` — file:///tmp/…, а даты, длительности и спикеров нет вовсе.
    Поля шапки уезжают в payload КАЖДОГО чанка и кормят инструмент `catalog` («кто выступал
    на митапе X») — наивный перенос ослепил бы каталожные вопросы;
  * `speakers` считает по телу: порядок первого появления, безымянные `Speaker_N` в шапку не идут;
  * держит сайдкар `record.json` СЫРЫМ — правки в него не запекаются. Иначе правку нельзя было
    бы отменить: убрал запись из словаря, а текст уже испорчен навсегда, пересобирать не из чего.
    Единственный источник правды о правках — словари, `record.md` из них выводится;
  * пишет файл, ТОЛЬКО если содержимое изменилось. Не косметика: индексатор решает «изменился ли
    документ» по mtime, и перезапись тем же текстом стоила бы переиндексации всего корпуса.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaces  # noqa: E402
import turn_edits  # noqa: E402

UTTERANCE_RE = re.compile(r"^\[([^\]]+)\] <!-- t:(\d+(?:\.\d+)?) --> ")

# Длинную реплику режем на абзацы. Не косметика: тайм-код в расшифровке стоит ОДИН на реплику, и
# чанкер морага, режа монолог на чанки, вычисляет время внутри него ИНТЕРПОЛЯЦИЕЙ по позиции в
# тексте. Замерено на нашем корпусе: медиана ошибки 2-57 с, худшая 110 с — то есть клик по цитате
# открыл бы читалку в двух минутах от нужного места. Абзац со своей МЕРЕНОЙ меткой это снимает,
# и движку не нужно знать ни про какой сайдкар: формат тот же, меток просто больше.
PARAGRAPH_SEC = 45.0       # цель длины абзаца
PARAGRAPH_MIN_PAUSE = 0.4  # короче — не пауза, а придыхание
# Окно поиска паузы вокруг цели. Искать только НАЗАД мало: если пауза в двух словах впереди,
# разрез уходит по живому тексту (поймано тестом). Поэтому набираем от 0.6 до 1.4 цели и режем
# по самой длинной паузе в этом окне.
PARAGRAPH_WINDOW = 60      # сколько слов назад ищем паузу
PARAGRAPH_HARD = 1.4       # во сколько раз дольше цели можно ждать паузу, если позади её нет
UNNAMED_RE = re.compile(r"^Speaker_\d+$")
DELIM = "---"
MEDIA_EXTS = ("mp4", "mkv", "mov", "webm", "avi", "m4v", "mp3", "m4a", "wav", "flac", "opus", "ogg")

# Транслитерация — только чтобы ПРЕДЛОЖИТЬ id по имени файла. Итоговый id выбирает человек:
# он становится адресом страницы и живёт дольше, чем имя файла на ноутбуке.
TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class Fail(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"make_record: {msg}")


# --- разбор и сборка шапки --------------------------------------------------

def split_head(text: str) -> tuple[str, str]:
    """`text` → (шапка вместе с обоими `---`, тело без ведущих переводов строки)."""
    if not text.startswith(DELIM):
        return "", text.lstrip("\n")
    end = text.find(f"\n{DELIM}", len(DELIM))
    if end == -1:
        raise Fail("шапка не закрыта")
    cut = end + 1 + len(DELIM)
    return text[:cut], text[cut:].lstrip("\n")


def parse_head(head: str) -> dict:
    """Плоская шапка `key: value` — ровно то, что пишем мы сами. Значения читаем как JSON."""
    out: dict = {}
    for line in head.splitlines():
        if line.strip() in (DELIM, ""):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def branch_for(record_dir: Path) -> str | None:
    """Ветка записи — каталог сразу под `records/`. Берём с ДИСКА, а не из правил.

    Правило могло поменяться после того, как запись легла на место, и тогда поле разошлось бы с
    путём. Истина — там, где файл: её же видит индексатор движка.
    """
    parts = record_dir.resolve().parts
    if "records" not in parts:
        return None
    tail = parts[parts.index("records") + 1:]
    return tail[0] if len(tail) > 2 else None

# Порядок полей шапки. Константа, а не литерал в цикле: по ней тест сверяет конфиг движка —
# поле фильтра `search(filters=…)`, которого нет в шапке, сужало бы поиск до нуля молча.
HEAD_ORDER = ("title", "date", "year", "event", "branch", "kind", "category", "topics", "speakers",
              "participants", "voices", "duration_sec", "summary", "media", "slides", "post",
              "discussion", "tags", "award")


def build_head(meta: dict) -> str:
    """Шапка записи. Порядок полей фиксирован — чтобы дифф правки был читаемым."""
    lines = [DELIM]
    # ⚠️ Шапка целиком уезжает в payload КАЖДОГО чанка и кормит каталожные вопросы («кто
    # выступал на X»). Поэтому новое поле стоит заводить ДО первой индексации: потом та же
    # правка означает переиндексацию всего пространства (изменение решается по mtime файла).
    # Люди — двумя списками (решение владельца 12.09): `speakers` — выступавшие, `participants`
    # — остальные названные (ведущие в том числе); `voices` — голосов всего, включая безымянные,
    # чтобы каталог видел, насколько списки неполны. `kind` и `award` — из календаря выступлений (`from == "calendar"` в мете).
    # Значения — строчным JSON: движок читает шапку построчно, YAML-списки не умеет.
    # `category`/`topics`/`year` — предмет и ось фильтра агента (12.09): категория ОДНА, темы —
    # список из словаря таксономии, год — строкой (фильтру движка дата неудобна).
    for key in HEAD_ORDER:
        value = meta.get(key)
        if value in (None, "", [], {}) or value is False:
            continue  # необязательное поле не пишем вовсе: пустое значение выглядит как ошибка
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append(DELIM)
    return "\n".join(lines)


def speakers_of(body: str) -> list[str]:
    """Названные голоса записи: порядок первого появления, безымянные `Speaker_N` отброшены.

    ⚠️ Это НЕ «выступавшие». Замерено 12.09: первым здесь стоит ведущий (он открывает встречу),
    а докладчик в 59% записей еженедельных докладов вовсе безымянный. Кто выступал, кто вёл и кто
    спрашивал — решает `roles_of`.
    """
    seen: list[str] = []
    for line in body.splitlines():
        m = UTTERANCE_RE.match(line)
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return [s for s in seen if not UNNAMED_RE.match(s)]


# Уменьшительные → полные: словарь имён пишет так, как человек представился («Люба»), календарь
# — как в справочнике («Любовь»). Список короткий и растёт по факту, а не «на будущее».
DIMINUTIVE = {"даша": "дарья", "саша": "александр", "женя": "евгений", "ваня": "иван",
              "миша": "михаил", "дима": "дмитрий", "люба": "любовь", "настя": "анастасия",
              "серёжа": "сергей", "сережа": "сергей", "лёша": "алексей", "леша": "алексей"}


def person_key(name: str) -> tuple[str, str]:
    """Ключ человека для сверки имён из разных источников: (фамилия, имя).

    Источники пишут людей по-разному: словарь имён — «Люба Кузнецова», календарь — «Любовь
    Кузнецова», метка поста — «Кузнецова». Фамилия — последнее слово; имени может не быть вовсе,
    тогда оно пустое и совпадает с любым. Ё и е — одна буква: «Пётр» и «Петр» — один человек;
    уменьшительное сводится к полному по `DIMINUTIVE`.
    """
    parts = name.replace("ё", "е").replace("Ё", "Е").split()
    if not parts:
        return ("", "")
    surname = parts[-1].lower()
    first = parts[0].lower() if len(parts) > 1 else ""
    return (surname, DIMINUTIVE.get(first, first))


def same_person(a: str, b: str) -> bool:
    ka, kb = person_key(a), person_key(b)
    return bool(ka[0]) and ka[0] == kb[0] and (not ka[1] or not kb[1] or ka[1] == kb[1])


def airtime_of(words: dict) -> dict[str, float]:
    """Эфир по видимой метке говорящего, секунды. Безымянные — под своими `Speaker_N`."""
    out: dict[str, float] = {}
    for turn in (words or {}).get("turns") or []:
        label = turn.get("speaker") or ""
        try:
            sec = float(turn.get("end") or 0) - float(turn.get("start") or 0)
        except (TypeError, ValueError):
            sec = 0.0
        out[label] = out.get(label, 0.0) + max(sec, 0.0)
    return out


# Доля эфира, с которой самый долгий названный голос считается докладчиком, когда ни календарь,
# ни мета его не назвали. Без порога докладчиком становился бы ведущий, открывший встречу, у
# записи с безымянным докладчиком (замерено: ведущий 30%, безымянный докладчик 60%).
SPEAKER_SHARE = 0.4


def labels_of(meta_json: dict) -> dict:
    """Категория, темы и формат из меты: `labels` (рука владельца) поверх `classification`
    (классификатор). Ничего, чего нет в мете, не сочиняем — пустое поле честнее догадки."""
    auto = (meta_json or {}).get("classification") or {}
    hand = (meta_json or {}).get("labels") or {}
    category = str(hand.get("category") or auto.get("category") or "").strip()
    topics = list(hand.get("topics") if hand.get("topics") is not None else auto.get("topics") or [])
    kind = str(hand.get("kind") or auto.get("kind") or "").strip()
    return {"category": category, "topics": [str(t) for t in topics if str(t).strip()], "kind": kind}


def year_of(date: str, record_dir: Path) -> str:
    """Год для фильтра. У курсов дата записи — дата ВЫКЛАДКИ (25 лекций «Python 2023» датированы
    одним днём 2024-го), поэтому там год берётся из второго уровня каталога («Python 2023»),
    а не из даты; у остальных — из даты."""
    parts = record_dir.resolve().parts
    if "records" in parts:
        tail = parts[parts.index("records") + 1:]
        if len(tail) > 2:
            m = re.search(r"(20\d\d)", tail[1])
            if m and not re.fullmatch(r"20\d\d", tail[1]):
                return m.group(1)
    return str(date or "")[:4]


def roles_of(body: str, words: dict, meta_json: dict, policy: dict) -> dict:
    """Кто выступал и кто участвовал — по модели ветки (`hub.yml::roles`).

    Два списка, не три (решение владельца 12.09: ведущие — просто участники; отдельная роль
    требовала списка фамилий и особых правил, а отвечала лишь на редкое «кто вёл»).
    Названные голоса — из тела (после словаря имён). Докладчики — по источникам, в порядке
    доверия: календарь выступлений → мета поста (метка, слайд, анонс) → самый долгий названный
    голос, если его доля эфира не меньше `SPEAKER_SHARE`. Имя источника сходится с голосом по
    `same_person`; сошлось — в шапку идёт имя ГОЛОСА (форма словаря), не сошлось — имя источника
    как есть: докладчик часто безымянен в звуке, но назван в календаре, и это тоже знание.
    ⚠️ Рука владельца (`roles` в мете) побеждает: имя уходит в свой список и убирается из другого.
    """
    named = speakers_of(body)
    airtime = airtime_of(words)
    total = sum(airtime.values()) or 1.0
    model = policy.get("model") or "meeting"
    meta_people = [s for s in (meta_json or {}).get("speakers") or [] if s.get("name")]

    def voice_for(name: str) -> str | None:
        return next((v for v in named if same_person(v, name)), None)

    def add(lst: list[str], name: str) -> None:
        if not any(same_person(x, name) for x in lst):
            lst.append(name)

    speakers: list[str] = []
    loudest = sorted(named, key=lambda v: -airtime.get(v, 0.0))[:1]
    if model == "talk":
        source = [s["name"] for s in meta_people if s.get("from") == "calendar"]
        if not source:
            source = [s["name"] for s in meta_people if s.get("from") in ("owner", "tag", "slide", "post")]
        if not source:
            source = [v for v in loudest if airtime.get(v, 0.0) / total >= SPEAKER_SHARE]
        for name in source:
            add(speakers, voice_for(name) or name)
    elif model == "course":
        for name in loudest + [s["name"] for s in meta_people if s.get("from") in ("calendar", "owner", "post")]:
            add(speakers, voice_for(name) or name)
    # meeting: докладчиков нет вовсе — все названные участники

    participants = [v for v in named if not any(same_person(v, x) for x in speakers)]

    # Рука владельца: список за списком, имя убирается из другого.
    override = (meta_json or {}).get("roles") or {}
    lists = {"speakers": speakers, "participants": participants}
    for role, names in override.items():
        if role not in lists:
            continue
        for name in names or []:
            for other, lst in lists.items():
                if other != role:
                    lst[:] = [x for x in lst if not same_person(x, name)]
            add(lists[role], voice_for(name) or name)

    ids = {t.get("speaker_id") or t.get("speaker") for t in (words or {}).get("turns") or []}
    ids.discard(None)
    voices = len(ids) if ids else len({m.group(1) for m in map(UTTERANCE_RE.match, body.splitlines()) if m})
    return {"speakers": speakers, "participants": participants, "voices": voices}


def slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        out.append(TRANSLIT.get(ch, ch if ch.isalnum() else "-"))
    return re.sub(r"-+", "-", "".join(out)).strip("-")


# --- правки -----------------------------------------------------------------

def apply_overrides(x: dict, overrides: dict[str, str]) -> list[dict]:
    """Проставление имён по `names.json`. Возвращает то, что реально применилось.

    Работает в двух режимах, и второй у нас основной. Если авто-наминг включён, в артефакте есть
    `speaker_names`, и мы ИСПРАВЛЯЕМ поставленное им имя. Если наминг выключен (а на митапе он
    выключен — подписывает не того), `speaker_names` пуст, метка в теле равна самому `Speaker_N`,
    и мы имя НАЗНАЧАЕМ. Ранняя версия умела только первое и молча не делала ничего.
    """
    names = x.get("speaker_names") or {}
    applied: list[dict] = []
    for sid, new_name in overrides.items():
        was = names.get(sid, sid)  # нет записи → метка в теле и есть идентификатор
        if was == new_name:
            continue  # конвейер и сам поставил верное имя — оверрайд стал историей, это норма
        twins = [s for s, n in names.items() if n == was and s != sid]
        if twins:
            raise Fail(f"метка {was!r} принадлежит не только {sid} (ещё {twins}) — переименование неоднозначно")
        # Считаем замены: имя из словаря, которого в этой записи нет, — не повод отчитываться
        # о применённой правке. Словарь общий на корпус, и большинства людей в конкретной записи нет.
        x["markdown"], hits = re.subn(
            rf"^\[{re.escape(was)}\] <!-- t:", f"[{new_name}] <!-- t:", x["markdown"], flags=re.M
        )
        if not hits:
            continue
        names[sid] = new_name
        for turn in x.get("turns") or []:
            if turn.get("speaker") == was:
                turn["speaker"] = new_name
        for turn in ((x.get("words") or {}).get("turns")) or []:
            if turn.get("speaker") == was:
                turn["speaker"] = new_name
        applied.append({"speaker_id": sid, "was": was, "now": new_name})
    if applied:
        # правка видна из самих данных — иначе через полгода разойдётся с реестром
        # и никто не вспомнит, откуда взялось имя
        x["name_overrides"] = applied
    return applied


# Чем кончается предложение. Кавычку и скобку учитываем: «…и всё.» — тоже конец.
SENTENCE_END = re.compile(r"[.!?…][»\"')\]]*$")


def sentence_end(word: str, nxt: str) -> bool:
    """Конец предложения между `word` и `nxt`.

    ⚠️ Мало точки: сокращения («т.е.», «и т.д.») её тоже носят, и разрез по ним рвал бы фразу
    ровно так же, как разрез по запинке. Поэтому требуем, чтобы СЛЕДУЮЩЕЕ слово начиналось
    с заглавной или с цифры — так выглядит настоящее начало предложения, а заодно и абзаца.
    """
    return bool(SENTENCE_END.search(word)) and bool(nxt) and (nxt[0].isupper() or nxt[0].isdigit())


def paragraphs_of(words: list, target: float = PARAGRAPH_SEC) -> list[list]:
    """Слова реплики → абзацы: копим до `target` секунд и режем по КОНЦУ ПРЕДЛОЖЕНИЯ в окне,
    а если его там нет — по самой длинной паузе.

    ⚠️ Предпочтение точки добавлено 08.09, и оно ОТМЕНЯЕТ прежний вывод. Раньше здесь стояло
    «самая длинная пауза и ЕСТЬ конец мысли, отдельно предпочитать точку не нужно» — с замером
    79% попаданий на конец предложения. Вывод был неверен: оставшийся 21% это 3705 разрезов по
    живой фразе на весь корпус, и в читалке они читаются как обрыв на полуслове. Замерено после
    правки: **95%** (14201 из 14897), посреди фразы осталось 696 — там, где точки нет вовсе
    («Всем привет ⟂ Ваня вроде бы сказал»).

    ⓘ Ещё одна поправка к тому же замеру, исторически первая: версия, дававшая 92%, считала ВСЕ
    концы абзацев, включая естественные концы реплик — они кончаются точкой сами собой. Честная
    цифра только по границам, которые СОЗДАЛ разрез.

    ⚠️ Правило разреза меняет `record.md` практически у всего корпуса (166 записей из 167),
    то есть после первой индексации будет стоить полного перестроения. Менять — до неё.
    """
    out, cur, start = [], [], words[0][1]
    for word in words:
        cur.append(word)
        elapsed = word[2] - start
        if elapsed < target:
            continue
        # Сначала ищем КОНЕЦ ПРЕДЛОЖЕНИЯ, и только если его в окне нет — самую длинную паузу.
        # Прежнее правило («самая длинная пауза и есть конец мысли») верно в четырёх случаях из
        # пяти, но оставшийся пятый читается плохо: абзац обрывается на середине фразы, и глаз
        # спотыкается. Замерено 08.09: 3705 таких разрезов из 17742.
        best, best_gap = None, PARAGRAPH_MIN_PAUSE
        window = range(max(1, len(cur) - PARAGRAPH_WINDOW), len(cur))
        for k in window:
            # Берём ПОСЛЕДНИЙ конец предложения в окне: он ближе всего к целевой длине, и
            # абзацы не становятся вдвое короче задуманного.
            if sentence_end(cur[k - 1][0], cur[k][0]):
                best, best_gap = k, None
        if best is None:
            for k in window:
                gap = cur[k][1] - cur[k - 1][2]
                if gap >= best_gap:
                    best, best_gap = k, gap
        # Пауз позади нет вовсе — не режем по живому, ждём ближайшую до жёсткого потолка.
        if best is None and elapsed < target * PARAGRAPH_HARD:
            continue
        cut = best if best is not None else len(cur)
        out.append(cur[:cut])
        cur = cur[cut:]
        if cur:
            start = cur[0][1]
    if cur:
        out.append(cur)
    return out


def split_long_turns(x: dict, target: float = PARAGRAPH_SEC) -> int:
    """Реплики длиннее `target` → абзацы с настоящими тайм-кодами. Возвращает число разрезов.

    Режем СРАЗУ И ТЕКСТ, И ВРЕМЕНА: они обязаны остаться одним и тем же разбиением, иначе читалка
    (она строит караоке из времён) разойдётся с расшифровкой. Нет пословных времён — не режем
    вовсе: без них честной метки не поставить, а выдуманная хуже отсутствующей.
    """
    head, body = split_head(x.get("markdown") or "")
    lines = [ln for ln in body.split("\n\n") if ln.strip()]
    turns = ((x.get("words") or {}).get("turns")) or []
    if not turns or len(lines) != len(turns):
        return 0  # расшифровка и времена разошлись — молча не режем

    out_lines, out_turns, cuts = [], [], 0
    for line, turn in zip(lines, turns):
        m = UTTERANCE_RE.match(line)
        words = turn.get("words") or []
        text = line[m.end():] if m else ""
        # Слова во времена приезжают тем же `text.split()`, но сверяемся: пунктуация или
        # правка словаря могли разойтись, и тогда резать нечем.
        if not m or not words or [w[0] for w in words] != text.split():
            out_lines.append(line)
            out_turns.append(turn)
            continue
        groups = paragraphs_of(words, target) if (turn["end"] - turn["start"]) > target * 1.5 else [words]
        if len(groups) == 1:
            out_lines.append(line)
            out_turns.append(turn)
            continue
        speaker = m.group(1)
        for group in groups:
            out_lines.append(f"[{speaker}] <!-- t:{group[0][1]:.1f} --> " + " ".join(w[0] for w in group))
            out_turns.append({"start": round(group[0][1], 2), "end": round(group[-1][2], 2),
                              "speaker": turn.get("speaker", ""),
                              # ⚠️ Идентификатор голоса переносим ЯВНО: реплика здесь собирается
                              # заново, и всё, что не перечислено, теряется молча.
                              "speaker_id": turn.get("speaker_id", ""), "words": group})
        cuts += len(groups) - 1

    x["markdown"] = (f"{head}\n\n" if head else "") + "\n\n".join(out_lines) + "\n"
    x["words"]["turns"] = out_turns
    return cuts


# Граница слова для замен. `\\b` в питоне на кириллице ведёт себя как надо только с юникодными
# классами, поэтому границу задаём явными lookaround'ами: `Кафка` не должна срабатывать внутри
# `Кафкианский`, а `Redis` — внутри `Rediscover`.
LEFT, RIGHT = r"(?<![^\W\d_])", r"(?![^\W\d_])"


def substitute(x: dict, rx: re.Pattern, repl) -> int:
    """Замена во ВСЕХ трёх местах разом: тело, реплики, пословные времена.

    ⚠️ Пословные времена — не довесок: караоке ищет слово по ним, и правка, дошедшая до текста,
    но не до слов, разводит текст и подсветку навсегда. Возвращает число замен в теле.
    """
    head, body = split_head(x["markdown"])
    body, n = rx.subn(repl, body)  # шапку не трогаем: её мы собираем сами
    x["markdown"] = f"{head}\n\n{body}" if head else body
    for turn in x.get("turns") or []:
        if turn.get("text"):
            turn["text"] = rx.sub(repl, turn["text"])
    for turn in ((x.get("words") or {}).get("turns")) or []:
        turn["words"] = [[rx.sub(repl, w[0]), *w[1:]] for w in turn.get("words") or []]
    return n


def apply_text_fixes(x: dict, fixes: list[dict]) -> list[dict]:
    """Пословные замены гарблов — в теле, репликах и словах. Ровно по границе слова.

    ⚠️ Замена обязана быть ОДНИМ словом. Пробел в ней создаёт «слово» с пробелом в пословных
    временах, и тогда `split_long_turns` перестаёт сверяться с текстом и молча не режет реплику
    на абзацы: часовой доклад становится одной простынёй с одним тайм-кодом, без ошибки в логе.
    Пустая замена — то же самое с другой стороны: она МЕНЯЕТ ЧИСЛО СЛОВ, а времена привязаны к
    словам. Убрать слово из реплики умеет правка реплики (`turn_fixes.json`), а не словарь.
    Проверка та же, что у цензуры; до появления правки с сайта её держала дисциплина.
    """
    applied: list[dict] = []
    for fix in fixes:
        was, now = fix["was"], fix["now"]
        if " " in now or not now.strip():
            raise Fail(f"правка «{was}»: замена {now!r} не одно слово — пословные времена получат "
                       f"«слово» с пробелом (или потеряют слово), и разрез на абзацы молча "
                       f"отключится. Удаление и вставку делает правка реплики, не словарь")
        n = substitute(x, re.compile(rf"{LEFT}{re.escape(was)}{RIGHT}"), now)
        if n:
            applied.append({"was": was, "now": now, "count": n})
    if applied:
        x["text_fixes"] = applied
    return applied


def apply_censor(x: dict, rules: list[dict]) -> list[dict]:
    """Цензурные замены: расслышано ВЕРНО, печатать не хотим.

    Отличие от гарблов одно, и оно в правиле, а не в механике: здесь задан КОРЕНЬ и ловится
    любая его форма, регистр не важен. Гарбл — конкретное написание, и там каждая форма идёт
    отдельной строкой; корень бы там навредил.

    ⚠️ Корень цепляет и законные слова с той же основой — на это есть `except`, и заполняется
    он по разбору (`tools/censor.py`), а не наугад.
    """
    applied: list[dict] = []
    for rule in rules:
        root, now = rule.get("root", ""), rule.get("now", "")
        if not root or not now:
            continue
        if " " in now:
            raise Fail(f"цензура «{root}»: в замене {now!r} пробел — пословные времена получат "
                       f"«слово» с пробелом, и разрез на абзацы молча отключится")
        skip = {w.lower() for w in (rule.get("except") or [])}

        def repl(m, _skip=skip, _now=now):
            return m.group(0) if m.group(0).lower() in _skip else _now

        # Хвост формы — только кириллица: цифры и латиница за корнем означают другое слово.
        rx = re.compile(rf"{LEFT}{re.escape(root)}[а-яё]*{RIGHT}", re.IGNORECASE)
        # ⚠️ Считаем замены В ТЕЛЕ, а не в замыкании: замена идёт в трёх местах разом (тело,
        # реплики, слова), и счётчик внутри `repl` показал бы утроенное число.
        n = substitute(x, rx, repl)
        if n:
            applied.append({"root": root, "now": now, "count": n})
    if applied:
        x["censored"] = applied
    return applied


# Вырожденный повтор: группа из 1-4 слов, повторённая подряд четыре раза и больше.
# ⚠️ Не «доля уникальных слов» — тот способ отвергнут замером: у длинной реплики доля падает сама
# собой, и порог 0.3 пометил треть корпуса. Повтор фразы — признак прямой.
DEGEN_RUN = re.compile(r"\b([\w'-]{2,}(?:\s+[\w'-]{2,}){0,3})(?:[,.!?…]?\s+\1\b){3,}", re.I | re.U)


def _collapse(text: str) -> str:
    """Схлопнуть вырожденный повтор до ОДНОГО вхождения."""
    return DEGEN_RUN.sub(lambda m: m.group(1), text)


def drop_degenerate(x: dict) -> int:
    """Убрать сочинения whisper на тишине — в теле, репликах и пословных временах.

    ⚠️ Схлопываем до одного вхождения, а НЕ вырезаем целиком: «да, да, да» бывает настоящим, и
    отличить его от галлюцинации по тексту нельзя. Один лишний токен дешевле потерянной речи.

    Замерено на курсе QA: 1758 слов мусора в 25 записях из 33 — «Извание Извание Извание»,
    «И И И И». Причина жанровая: онлайн-курс, где лектор замолкает на практику, а whisper на
    тишине не молчит, а сочиняет. `trim_tail.py` тут бессилен — тишина в СЕРЕДИНЕ, а он режет
    хвост; резать же внутри записи нельзя, это сдвинет все тайм-коды и разъедет караоке с видео.

    Слова чистятся по тем же границам, что и текст: склеиваем токены реплики, ищем повтор,
    выбрасываем попавшие в него индексы. Иначе подсветка показывала бы слова, которых в тексте
    уже нет.
    """
    removed = 0
    head, body = split_head(x["markdown"])
    new_body = _collapse(body)
    removed += len(body.split()) - len(new_body.split())
    x["markdown"] = f"{head}\n\n{new_body}" if head else new_body
    for turn in x.get("turns") or []:
        if turn.get("text"):
            turn["text"] = _collapse(turn["text"])
    for turn in ((x.get("words") or {}).get("turns")) or []:
        ws = turn.get("words") or []
        if not ws:
            continue
        joined, offsets, pos = [], [], 0
        for w in ws:
            offsets.append(pos)
            joined.append(w[0])
            pos += len(w[0]) + 1
        text = " ".join(joined)
        cut = set()
        for m in DEGEN_RUN.finditer(text):
            keep_end = m.start() + len(m.group(1))
            for i, off in enumerate(offsets):
                if keep_end <= off < m.end():
                    cut.add(i)
        if cut:
            turn["words"] = [w for i, w in enumerate(ws) if i not in cut]
    return removed


def load_dicts(corpus: Path, record_id: str) -> tuple[dict[str, str], list[dict], list[dict]]:
    """Правки для записи: имена, гарблы (общие плюс адресные) и цензура.

    Цензура возвращается ОТДЕЛЬНО от гарблов, а не сливается с ними: у неё другое правило
    (корень вместо написания) и другие потребители — канон корпуса (`make_meta.corpus_canon`) и
    проверка утечек (`leak_check.forbidden`) её видеть не должны, см. `censor._why` в словаре.
    """
    names: dict[str, str] = {}
    path = corpus / "names.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        names.update(data.get("speakers") or {})
        names.update((data.get("records") or {}).get(record_id) or {})
    fixes: list[dict] = []
    censor: list[dict] = []
    path = corpus / "text_fixes.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        fixes += list(data.get("global") or [])
        fixes += list((data.get("records") or {}).get(record_id) or [])
        censor += list((data.get("censor") or {}).get("rules") or [])
    return names, fixes, censor


# --- проверки ---------------------------------------------------------------

def check_words(words: dict) -> list[str]:
    """Порядок слов во времени — обязательство перед фронтом, а не пожелание.

    Караоке ищет текущее слово ДВОИЧНЫМ поиском и на неотсортированном врёт молча.
    """
    problems = []
    total = 0
    for i, turn in enumerate(words.get("turns") or []):
        prev = -1.0
        for w in turn.get("words") or []:
            total += 1
            if w[1] < prev - 1e-6:
                problems.append(f"реплика {i}: слово {w[0]!r} начинается раньше предыдущего")
                break
            prev = w[1]
    if not total:
        problems.append("пословных тайм-кодов нет вовсе — караоке работать не будет")
    return problems


def write_if_changed(path: Path, text: str, dry: bool) -> bool:
    """Пишем, только если содержимое изменилось (иначе mtime дёрнет переиндексацию корпуса)."""
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old == text:
        return False
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return True


# --- сборка -----------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="артефакт транскрибации → каталог записи")
    ap.add_argument("source", nargs="?",
                    help="артефакт <имя>.json из inbox/ либо готовый каталог записи")
    ap.add_argument("--id", help="идентификатор записи = имя каталога (он же адрес страницы)")
    ap.add_argument("--title", help="заголовок доклада, без префиксов")
    ap.add_argument("--date", help="дата записи, YYYY-MM-DD")
    ap.add_argument("--event", help="митап, к которому относится доклад (ось группировки)")
    ap.add_argument("--tags", help="темы через запятую")
    ap.add_argument("--summary", help="аннотация: о чём доклад (текст поста)")
    ap.add_argument("--post", help="ссылка на исходный пост")
    ap.add_argument("--no-media", action="store_true", help="не переносить видео в media/")
    ap.add_argument("--media", help="имя (или путь внутри media/) видео, когда его нет локально")
    ap.add_argument("--dry", action="store_true", help="показать, что будет сделано")
    # ⚠️ Пересборка ВСЕГО корпуса — операция, которую не стоит держать рукописным циклом в
    # истории команд: она трогает каждый record.md, а значит и mtime, по которому индексатор
    # решает «документ изменился». Пусть у неё будет имя и `--dry`.
    ap.add_argument("--all", action="store_true",
                    help="пересобрать все записи корпуса из их сайдкаров")
    args = ap.parse_args()

    if args.all:
        if args.source:
            raise Fail("--all и указанный источник вместе не имеют смысла")
        # ⚠️ Рекурсивно и через общий помощник: записи лежат по веткам и годам, а обход
        # корней на один уровень видел бы ВЕТКИ и молча пересобирал ноль записей.
        dirs = spaces.record_dirs("record.json")
        if not dirs:
            raise Fail("не нашлось ни одной записи с сайдкаром — пересобирать не из чего")
        print(f"пересборка всего корпуса: {len(dirs)} записей"
              + (" (--dry: ничего не пишу)" if args.dry else ""))
        touched = 0
        for d in dirs:
            changed: list[str] = []
            build_one(args, d, changed)
            touched += bool(changed)
        print(f"\nзаписей затронуто: {touched} из {len(dirs)}")
        return 0

    if not args.source:
        raise Fail("нужен источник — артефакт из inbox/ или каталог записи (или --all)")
    return build_one(args, Path(args.source))


def build_one(args, source: Path, changed_out: list | None = None) -> int:
    """Собрать или пересобрать ОДНУ запись. `source` — артефакт из inbox/ либо каталог.

    ⚠️ Что изменилось, отдаём списком через `changed_out`, а не измеряем снаружи по mtime:
    в `--dry` файл не пишется вовсе, и внешний замер показал бы «не изменилось ничего» именно
    тогда, когда разбор и нужен.
    """
    rebuild = source.is_dir()
    if rebuild:
        record_dir = source
        artifact = record_dir / "record.json"
        if not artifact.is_file():
            raise Fail(f"нет сайдкара {artifact} — пересобирать не из чего, гоните из inbox/")
        record_id = args.id or record_dir.name
        # ⚠️ Не `parent.parent`: после раскладки по пространствам такой подъём упирается в
        # каталог пространства, словари нашлись бы пустыми, и правки молча перестали бы
        # применяться. Ищем по признаку — вверх до каталога со словарями.
        corpus = spaces.corpus_root(record_dir)
        old_head = parse_head(split_head((record_dir / "record.md").read_text(encoding="utf-8"))[0]) \
            if (record_dir / "record.md").is_file() else {}
    else:
        artifact = source
        if not artifact.is_file():
            raise Fail(f"не нашёл артефакт: {artifact}")
        corpus = spaces.corpus_root(artifact)
        record_id = args.id or ""
        if not record_id:
            suggest = slugify(artifact.stem)
            raise Fail(f"нужен --id (он станет адресом страницы). По имени файла подошёл бы: "
                       f"--id {args.date or 'ГГГГ-ММ-ДД'}-{suggest}")
        # Куда класть новую запись, решает рубрика: у каждого пространства свой каталог, он
        # же `sources[].path` его индексатора. Правило — одно, в hub.yml.
        # ⚠️ Дату передаём обязательно: она даёт ВТОРОЙ уровень раскладки (год). Забудешь — год
        # окажется пустым, запись ляжет плоско мимо своей ветки, и разъезд с правилами всплывёт
        # только на инварианте раскладки, уже после переноса партии.
        route_date = args.date or ""
        if not route_date:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", record_id)
            route_date = m.group(1) if m else ""
        record_dir = spaces.route(
            record_id, family=corpus, event=args.event or "", date=route_date,
            tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()])
        old_head = {}

    x = json.loads(artifact.read_text(encoding="utf-8")).get("x_enriched")
    if not x or not x.get("markdown"):
        raise Fail(f"в {artifact.name} нет x_enriched.markdown — это не артефакт адаптера")

    names, fixes, censor = load_dicts(corpus, record_id)
    # Мета записи: при пересборке лежит рядом, у новой — ещё в inbox рядом с артефактом.
    meta_path = record_dir / "record.meta.json" if rebuild else artifact.with_suffix(".meta.json")
    try:
        meta_json = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    except json.JSONDecodeError:
        meta_json = {}
    talk = meta_json.get("talk") or {}
    # ⚠️ Идентификатор голоса проставляем ДО переименования и держим рядом с именем. Он общий на
    # весь корпус (это ключ реестра голосов), и без него сайт, увидев в караоке имя, не смог бы
    # сказать, ЧЕЙ это голос: имена не уникальны, а метка после правки исчезает вовсе.
    for turn in ((x.get("words") or {}).get("turns")) or []:
        turn.setdefault("speaker_id", turn.get("speaker") or "")
    renamed = apply_overrides(x, names)
    # Правка реплики с сайта — ДО словарей: тогда правленая реплика остаётся живой для будущих
    # правил (заведём завтра `Maraqui → Morag` — починится и внутри неё). Обратный порядок
    # заморозил бы реплику ЦЕЛИКОМ, включая слова, которых человек не касался.
    edited = turn_edits.apply(x, turn_edits.load(corpus, record_id), warn=lambda m: print(f"  {m}"))
    fixed = apply_text_fixes(x, fixes)
    # Цензура ПОСЛЕ гарблов: правка гарбла теоретически может открыть слово, печатать которое
    # мы не хотим, а обратный порядок такой случай пропустил бы.
    censored = apply_censor(x, censor)
    # Чистка сочинений на тишине — ДО разреза на абзацы: метки абзацев считаются по словам, и
    # считать их по мусору незачем. Сайдкар остаётся сырым, поэтому правка обратима пересборкой.
    degen = drop_degenerate(x)
    # Абзацы режем ПОСЛЕ правок словарей: те работают по токенам, и разрез им безразличен,
    # а вот метки абзацев обязаны считаться по уже поправленным словам.
    paragraphs = split_long_turns(x)

    _, body = split_head(x["markdown"])
    words = x.get("words") or {}
    has_words = bool(words.get("turns"))
    if has_words:
        words["episode"] = record_id  # поле формата morag-words-v1; у нас в нём id записи

    # шапку собираем сами: у артефакта она вырожденная (см. докстринг)
    roles = roles_of(body, words, meta_json, spaces.roles_policy(branch_for(record_dir) or "", corpus))
    labels = labels_of(meta_json)
    meta = {
        "title": args.title or old_head.get("title") or artifact.stem,
        # Дата ВЫСТУПЛЕНИЯ из календаря выступлений, если строка нашлась (решение владельца
        # 12.09): дата поста отстаёт на дни, а бывает и на месяцы. id и адрес не меняются.
        "date": args.date or talk.get("date") or old_head.get("date"),
        "event": args.event or old_head.get("event"),
        # Ветка = первый уровень раскладки на диске. Пишем её ПОЛЕМ, хотя она уже есть в пути,
        # и это не дубль по недосмотру: путь входит только в dense-вектор, а АГЕНТ его не видит
        # — в нашей выдаче печатается момент записи («доклад · MM:SS · спикер»), строки «Путь:»
        # там нет. Поле же уезжает в payload каждого чанка и в `catalog`, то есть отвечает на
        # «сколько собраний было в 2025». Правило ветки одно на репозиторий (`hub.yml::routing`),
        # применить его сам агент не может.
        "branch": branch_for(record_dir),
        # Формат: календарь выступлений главнее; где календаря нет — классификатор.
        "kind": list(talk.get("kind") or ([labels["kind"]] if labels["kind"] else [])),
        "category": labels["category"],
        "topics": labels["topics"],
        "speakers": roles["speakers"],
        "participants": roles["participants"],
        "voices": roles["voices"],
        "duration_sec": round(float((x.get("coverage") or {}).get("audio_sec") or 0)) or None,
        # --media задаётся явно для перенесённых с прежнего сайта записей: видео там остаётся на
        # сервере (194 ГБ архива), а на ноутбук приезжает только звук для расшифровки
        "media": args.media or old_head.get("media"),
        # Аннотация доклада — авторский текст поста, а не пересказ расшифровки: в списке из
        # 167 заголовков она единственное, по чему видно, о чём запись.
        "summary": args.summary or old_head.get("summary"),
        # Ссылка на исходный пост: расшифровка не заменяет обсуждение под ним.
        "post": args.post or old_head.get("post"),
        # Обсуждение в мессенджере — из меты (полный текст поста и календарь), не из старой шапки:
        # мета пересобирается, а шапка выводится из неё.
        "discussion": ((meta_json.get("links") or {}).get("discussion")) or old_head.get("discussion"),
        "slides": old_head.get("slides"),
        "tags": [t.strip() for t in args.tags.split(",")] if args.tags else old_head.get("tags"),
        "award": bool(talk.get("award")),
    }
    if not meta["date"]:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", record_id)
        meta["date"] = m.group(1) if m else None
    if not meta["date"]:
        raise Fail("нужна --date: дата уезжает в каждый чанк и кормит каталожные вопросы")
    meta["year"] = year_of(meta["date"], record_dir)

    # медиа: переносим из inbox/ в media/ под именем записи
    if not rebuild and not args.no_media and not args.media:
        for ext in MEDIA_EXTS:
            src = artifact.with_suffix(f".{ext}")
            if src.is_file():
                dst = corpus / "media" / f"{record_id}.{ext}"
                if dst.exists():
                    print(f"    медиа уже на месте: media/{dst.name}")
                elif not args.dry:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                meta["media"] = dst.name
                break
    if not meta["media"]:
        print("  ⚠️ видео не найдено — запись будет без плеера (для перенесённого анонса это норма)")

    # Слайды: тот файл, который САЙТ умеет показать, то есть PDF. Прежнее правило требовало
    # ЕДИНСТВЕННЫЙ pdf и не знало про pptx — записи с двумя колодами теряли слайды вовсе, и поле
    # стояло у 13% записей при 28% файлов на диске. Поле остаётся одной строкой: `slides` уезжает
    # в шапку и в API как имя файла, а список сломал бы и то, и другое.
    if not meta["slides"] and record_dir.is_dir():
        pdfs = sorted(record_dir.glob("*.pdf"))
        decks = sorted(record_dir.glob("*.pptx"))
        if pdfs:
            meta["slides"] = pdfs[0].name
            if len(pdfs) > 1:
                print(f"  ⚠️ колод несколько, показываем первую: "
                      f"{', '.join(p.name for p in pdfs)}")
        elif decks:
            print(f"  ⚠️ слайды только в pptx ({decks[0].name}) — браузер их не покажет, "
                  f"нужен экспорт в PDF")

    # Мета записи (что мы знали о ней ДО расшифровки) переезжает из inbox вместе с записью:
    # дальше она живёт рядом с расшифровкой и кормит будущую карточку спикера. Формат —
    # `docs/record-metadata.md`, собирает `tools/make_meta.py`.
    if not rebuild and not args.dry:
        src_meta = artifact.with_suffix(".meta.json")
        if src_meta.is_file():
            record_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_meta), str(record_dir / "record.meta.json"))
            print("    мета записи перенесена: record.meta.json")
            artifact.with_suffix(".hints.json").unlink(missing_ok=True)

    for problem in check_words(words):
        print(f"  ⚠️ {problem}")

    record_md = build_head(meta) + "\n\n" + body.rstrip("\n") + "\n"
    changed = []
    if write_if_changed(record_dir / "record.md", record_md, args.dry):
        changed.append("record.md")
    # ⚠️ Пустой файл пословных времён НЕ пишем. Караоке и перемотка по слову решают «умеем ли мы
    # это для записи» по наличию файла: файл-пустышка — молчаливый обман, сайт покажет управление,
    # которое ничего не делает. Лучше честное «выравнивания нет», чем неработающая кнопка.
    stale = record_dir / "record.words.json"
    if has_words:
        if write_if_changed(stale, json.dumps(words, ensure_ascii=False, indent=1) + "\n", args.dry):
            changed.append("record.words.json")
    elif stale.is_file():
        print("  ⚠️ прежний record.words.json остался, а в артефакте времён нет — он протух "
              "и разъедется с текстом; удалите его или переснимите запись с выравниванием")
    if not rebuild and not args.dry:
        record_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(artifact), str(record_dir / "record.json"))
        changed.append("record.json")

    print(f"{'[dry] ' if args.dry else ''}{record_id}: {meta['title']}")
    print(f"  дата {meta['date']} · {meta.get('event') or 'без митапа'} · "
          f"{meta['duration_sec']}с · докладчики: {', '.join(meta['speakers']) or '—'}"
          + (f" · участники: {', '.join(meta['participants'])}" if meta['participants'] else "")
          + f" · голосов {meta['voices']}")
    # Что стоит глаза владельца: доклад без названного докладчика и имя из календаря, которое
    # не сошлось ни с одним голосом (докладчик безымянен в звуке или назван иначе).
    policy_model = spaces.roles_policy(branch_for(record_dir) or "", corpus).get("model")
    if policy_model == "talk" and not meta["speakers"]:
        print("  ⚠️ докладчик не назван: ни календаря, ни меты, ни названного голоса")
    for entry in (meta_json.get("speakers") or []):
        if entry.get("from") == "calendar" and not any(same_person(v, entry["name"]) for v in speakers_of(body)):
            print(f"  ⓘ по календарю выступал(а) {entry['name']} — голос в записи не назван")
    if degen:
        print(f"    вырожденных повторов схлопнуто: {degen} слов")
    if renamed:
        moves = ", ".join(f"{r['was']}→{r['now']}" for r in renamed)
        print(f"  наминг поправлен: {moves}")
    if edited:
        print(f"  правок реплик: {len(edited)}")
    if fixed:
        print(f"  правок текста: {sum(f['count'] for f in fixed)}")
    if censored:
        print(f"  цензура: {sum(c['count'] for c in censored)}")
    if paragraphs:
        print(f"  длинные реплики разрезаны на абзацы: {paragraphs} разрезов по паузам")
    print(f"  {'записано: ' + ', '.join(changed) if changed else 'изменений нет'}")
    if changed_out is not None:
        changed_out.extend(changed)

    # ⚠️ Сайдкар уехал в каталог записи, и по нему run_folder.sh отличал сделанное от несделанного.
    # Если исходник остался в inbox/ — следующий прогон расшифрует его ЗАНОВО (это 20 минут и
    # лишние вызовы LLM), причём молча, как будто так и надо.
    if not args.dry:
        left = [p for e in MEDIA_EXTS for p in [artifact.with_suffix(f".{e}")] if p.is_file()]
        for p in left:
            print(f"  ⚠️ исходник остался в inbox/: {p.name} — уберите его, иначе прогон повторится")
    return 0


if __name__ == "__main__":
    sys.exit(main())
