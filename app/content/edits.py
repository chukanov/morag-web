"""Правка реплики с сайта: проверка, запись в словарь и повышение правки до правила корпуса.

Три вещи, которые определяют здесь всё остальное:

* **правим кусок КОНКРЕТНОЙ реплики**, а `text_fixes.json` остаётся тем, чем был — правилом на
  весь корпус. Замена, удаление и вставка не задаются командами, а ВЫВОДЯТСЯ из разницы
  «было/стало»: словарь умеет только «написание → написание», один токен в один;
* **правка ложится в координаты сырца**, а человек видит текст после гарблов, цензуры и разреза
  на абзацы. Перевод между ними делает `tools/turn_edits.py`;
* **трогаем только то, что человек изменил.** Слова, которых он не касался, переносятся в новую
  реплику как есть — вместе со своим временем и вместе с гарблом, если тот там был. Иначе правка
  опечатки молча вмораживала бы в сырец все действующие правила, и снятие правила эту реплику уже
  не чинило бы.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import turn_edits  # noqa: E402

TURN_FIXES = "turn_fixes.json"
TEXT_FIXES = "text_fixes.json"

WHY = "правка владельца, {day}, локально. ⚠️ Автор неизвестен: авторизации пока нет."


class Stale(Exception):
    """Человек правил не то, что лежит сейчас. Ответ — 409, а не тихая правка соседнего места."""


class Refused(Exception):
    """Правку принять нельзя по существу: пустая реплика, замена с пробелом, нет такого абзаца."""


def _write(path: Path, data: dict) -> None:
    """⚠️ Через временный файл: словарь — единственный источник правды о правках, и оборванная
    запись стоила бы всех разом. Тот же приём, что в `voices.rename`."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.exception("словарь не читается: %s", path)
        raise Refused(f"словарь {path.name} не читается — правка не записана")


def splice(shown: list[list], now: list[str], raw: list[str], span: tuple[int, int]) -> str:
    """Новый текст КУСКА СЫРЦА: разница «было/стало» переносится в координаты сырца.

    `shown` — слова абзаца, как их видел человек (каждое несёт свой адрес в реплике сырца);
    `now` — что он написал; `raw` — слова реплики сырца; `span` — границы куска в ней.

    ⚠️ Слово, которого правка не коснулась, в сырце НЕ ТРОГАЕМ. Поэтому переносим не текст
    абзаца целиком, а только изменённые куски: сырец и показанный текст различаются гарблами и
    цензурой, и «перепишем целиком» вморозило бы их навсегда.
    """
    lo, hi = span
    old = [w[0] for w in shown]
    addr = [w[turn_edits.ADDR][1] for w in shown]
    pieces: list[tuple[int, int, list[str]]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=old, b=now, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        at = addr[i1] if i1 < len(addr) else hi
        end = addr[i2 - 1] + 1 if i2 > i1 else at
        pieces.append((at, end, now[j1:j2]))
    out = list(raw[lo:hi])
    for at, end, texts in reversed(pieces):  # с конца: иначе поедут индексы следующих кусков
        out[at - lo:end - lo] = texts
    return " ".join(out)


class Edits:
    """Словарь правок реплик одной семьи пространств."""

    def __init__(self, family: Path) -> None:
        self.family = Path(family)

    @property
    def path(self) -> Path:
        return self.family / TURN_FIXES

    def of(self, record_id: str) -> list[dict]:
        return list((_read(self.path).get("records") or {}).get(record_id) or [])

    # --- запись правок -----------------------------------------------------

    def save(self, record_dir: Path, items: list[dict], why: str = "") -> dict:
        """Принять правки абзацев записи. Возвращает записанное и то, что стоит спросить.

        `items` — `[{"turn": <номер абзаца>, "was": <что человек видел>, "now": <что написал>}]`.
        Номер абзаца — индекс в `record.words.json`, ровно тот, что видит читалка.
        `why` — подпись правившего; пусто — «автор неизвестен» (авторизации нет).
        """
        record_id = record_dir.name
        try:
            x, sl, base = turn_edits.trace(record_dir)
        except turn_edits.EditError as error:
            # Нет сырца — правка невозможна по существу, а не «попробуйте позже»: 400 с причиной.
            raise Refused(str(error)) from error
        shown = ((x.get("words") or {}).get("turns")) or []

        fresh: list[dict] = []
        for item in items:
            i = item.get("turn")
            if not isinstance(i, int) or not 0 <= i < len(shown):
                raise Stale(f"абзаца {i} в записи нет — страницу надо перечитать")
            seen = " ".join(w[0] for w in shown[i]["words"])
            if seen != " ".join(str(item.get("was", "")).split()):
                raise Stale(f"абзац {i} на сервере уже другой — страницу надо перечитать")
            now = str(item.get("now", "")).split()
            if not now:
                raise Refused("пустая реплика: у неё нет ни слов, ни границ, караоке на ней "
                              "спотыкается. Неверная целиком реплика — это другой разговор")
            if " ".join(now) == seen:
                continue  # ничего не менялось — молча пропускаем, а не пишем пустышку
            where = sl[i]
            if where is None:
                raise Refused(f"в абзаце {i} нет пословных времён — править его нечем")
            t, lo, hi = where["turn"], where["from"], where["to"] + 1
            was_raw = " ".join(base[t][lo:hi])
            now_raw = splice(shown[i]["words"], now, base[t], (lo, hi))
            if now_raw == was_raw:
                continue
            fresh.append({"turn": t, "at": lo, "was": was_raw, "now": now_raw,
                          "why": why or WHY.format(day=date.today().isoformat())})

        if fresh:
            raw = _read(self.path)
            raw.setdefault("_doc", "Правки реплик с сайта. Ключ — запись; `was` ищется как подряд "
                                   "идущие слова в реплике сырца, не нашлось — правка не "
                                   "применяется и говорит об этом. Убрать строку = отменить правку.")
            raw.setdefault("records", {})
            raw["records"].setdefault(record_id, []).extend(fresh)
            _write(self.path, raw)

        return {"record": record_id, "saved": fresh, "suggest": self.suggest(fresh)}

    def drop(self, record_id: str) -> int:
        """Снять ВСЕ правки записи. Обратимость — операция, а не обещание."""
        raw = _read(self.path)
        gone = len((raw.get("records") or {}).pop(record_id, []))
        if gone:
            _write(self.path, raw)
        return gone

    # --- повышение до правила корпуса --------------------------------------

    @staticmethod
    def suggest(saved: list[dict]) -> list[dict]:
        """Замены слова на слово внутри правки — кандидаты на «починить везде».

        Только они: удаление и вставку правилом не выразить, у словаря нет для этого формы.
        """
        out: list[dict] = []
        for edit in saved:
            was, now = edit["was"].split(), edit["now"].split()
            for tag, i1, i2, j1, j2 in SequenceMatcher(a=was, b=now,
                                                       autojunk=False).get_opcodes():
                if tag != "replace" or i2 - i1 != 1 or j2 - j1 != 1:
                    continue
                pair = {"was": was[i1], "now": now[j1]}
                if pair not in out:
                    out.append(pair)
        return out

    def promote(self, record_id: str, was: str, now: str, why: str = "") -> dict:
        """Завести правило на весь корпус — и снять поместную правку, если она вся про него.

        ⚠️ Оставить оба значило бы чинить одно двумя механизмами: снятое потом правило не
        вернуло бы реплике исходный текст, потому что её держала бы ещё и поместная правка.
        """
        was, now = was.strip(), now.strip()
        if not was or not now:
            raise Refused("правило должно быть «написание → написание»")
        if " " in was or " " in now:
            raise Refused("правило только на ОДНО слово: пробел в замене создаёт «слово» с "
                          "пробелом в пословных временах, и разрез на абзацы молча отключается")
        if was == now:
            raise Refused("левая и правая части правила совпадают")

        path = self.family / TEXT_FIXES
        raw = _read(path)
        rules = raw.setdefault("global", [])
        if any(r.get("was") == was for r in rules):
            raise Refused(f"правило на «{was}» в словаре уже есть — посмотрите его")
        rules.append({"was": was, "now": now,
                      "why": why or WHY.format(day=date.today().isoformat())})
        _write(path, raw)

        dropped = self._forget(record_id, was, now)
        return {"was": was, "now": now, "dropped": dropped}

    def _forget(self, record_id: str, was: str, now: str) -> int:
        """Убрать поместные правки записи, которые целиком сводятся к этой одной замене."""
        raw = _read(self.path)
        kept, gone = [], 0
        for edit in (raw.get("records") or {}).get(record_id) or []:
            a, b = edit["was"].split(), edit["now"].split()
            same = len(a) == len(b) and all(
                x == y or (x == was and y == now) for x, y in zip(a, b))
            if same and a != b:
                gone += 1
                continue
            kept.append(edit)
        if gone:
            if kept:
                raw["records"][record_id] = kept
            else:
                raw["records"].pop(record_id, None)
            _write(self.path, raw)
        return gone
