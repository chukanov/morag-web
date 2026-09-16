"""Голоса корпуса: снимок для верстака имён и запись правки в словарь.

Пространство `Speaker_N` — ОБЩЕЕ на весь корпус: это идентификатор из реестра голосов
машины транскрибации, а не метка внутри одной записи. Отсюда главное свойство: имя, названное
один раз, относится ко всем записям, где голос звучит. Инструмент этим и ценен — у самого
частого голоса корпуса 47 записей.

Что здесь есть и чего намеренно нет:
  * снимок (`voices.json`) — ПРОИЗВОДНЫЙ и собирается инструментом `tools/voices.py`; читаем
    его, но не строим: он требует сырых сайдкаров, которых на сервере нет;
  * запись идёт в `names.json` — тот же словарь, что читает сборка записи. Никакой «правки на
    отображении» не существует: имя доезжает до текста, до шапки и до поиска через пересборку;
  * обратимость не отдельная функция, а следствие: пустое имя убирает запись из словаря, и
    пересборка возвращает `Speaker_N`, потому что сайдкар остался сырым.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

SNAPSHOT = "voices.json"
NAMES = "names.json"
# Метка безымянного голоса. Тот же вид, что у `make_record.UNNAMED_RE`: держать два разных
# представления одной вещи — верный способ разойтись.
VOICE_RE = re.compile(r"^Speaker_\d+$")


class Voices:
    """Снимок голосов одной семьи пространств. Перечитывается по mtime файла."""

    def __init__(self, family: Path) -> None:
        self.family = Path(family)
        self._data: dict = {}
        self._stamp: float = -1.0

    @property
    def path(self) -> Path:
        return self.family / SNAPSHOT

    @property
    def names_path(self) -> Path:
        return self.family / NAMES

    def data(self) -> dict:
        """Снимок или пусто. Нет файла — не ошибка: его собирают инструментом."""
        path = self.path
        if not path.is_file():
            return {}
        stamp = path.stat().st_mtime
        if stamp != self._stamp:
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.exception("снимок голосов не читается: %s", path)
                self._data = {}
            self._stamp = stamp
        return self._data

    def public(self) -> dict:
        """То, что уезжает в браузер. Имена берём из СЛОВАРЯ, а не из снимка.

        ⚠️ Снимок стареет: его собирают вручную, а словарь меняется на каждой правке. Показывать
        имя из снимка значило бы врать сразу после первой же правки — до следующего `--scan`.
        """
        data = self.data()
        named, why = self.names()
        voices = []
        for v in data.get("voices") or []:
            voices.append({**v, "name": named.get(v["id"], ""), "why": why.get(v["id"], "")})
        return {
            "ready": bool(data),
            "vocabulary": data.get("vocabulary") or sorted(set(named.values())),
            "record_space": data.get("record_space") or {},
            "voices": voices,
        }

    def one(self, voice: str, record: str = "") -> dict:
        """Один голос — для правки ПРЯМО В ЧИТАЛКЕ, без похода в верстак.

        Отдаём только то, что нужно, чтобы назвать этот голос: сколько он звучит, как
        представлялся сам, кого предлагает мета записей и какие имена вообще есть в корпусе.
        Весь снимок (281 голос, 300 КБ) ради одной метки в браузер гонять незачем.

        `record` — запись, из которой пришли: её собственные кандидаты идут ПЕРВЫМИ. Мета
        называет докладчика именно этой встречи, и это самая близкая догадка из возможных.
        """
        data = self.data()
        named, why = self.names()
        found = next((v for v in data.get("voices") or [] if v.get("id") == voice), None)
        candidates = list((found or {}).get("candidates") or [])
        if record:
            свои = [c for c in candidates if c.get("from") == record]
            candidates = свои + [c for c in candidates if c not in свои]
        return {
            "id": voice,
            "known": bool(found),
            "name": named.get(voice, ""),
            "why": why.get(voice, ""),
            "sec": (found or {}).get("sec", 0),
            "records": list((found or {}).get("records") or []),
            "spaces": list((found or {}).get("spaces") or []),
            # Самопредставление — сильнейший источник: «меня зовут…» есть у 27 голосов из топ-30.
            "intros": list((found or {}).get("intros") or []),
            "conflict": list((found or {}).get("conflict") or []),
            "candidates": candidates,
            "vocabulary": data.get("vocabulary") or sorted(set(named.values())),
        }

    # --- словарь имён ------------------------------------------------------

    def names(self) -> tuple[dict[str, str], dict[str, str]]:
        path = self.names_path
        if not path.is_file():
            return {}, {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.exception("словарь имён не читается: %s", path)
            return {}, {}
        return dict(raw.get("speakers") or {}), dict(raw.get("_why") or {})

    def rename(self, voice: str, name: str, why: str, record: str = "") -> list[str]:
        """Записать имя в словарь. Возвращает записи, которые надо пересобрать.

        `record` задан — правка адресная (`records[<id>]`), и она нужна там, где реестр склеил
        двух людей: глобальное имя тогда подписало бы обоих.

        Пустое имя = снять правку. Это и есть обратимость: словарь снова молчит, а текст
        выводится из словаря, поэтому пересборка вернёт `Speaker_N`.
        """
        if not VOICE_RE.match(voice):
            raise ValueError(f"не похоже на идентификатор голоса: {voice!r}")
        path = self.names_path
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        raw.setdefault("speakers", {})
        raw.setdefault("records", {})
        raw.setdefault("_why", {})

        target = raw["records"].setdefault(record, {}) if record else raw["speakers"]
        if name:
            target[voice] = name
        else:
            target.pop(voice, None)
        # Пустая адресная секция — мусор: словарь читают глазами, и `{}` в нём только мешает.
        if record and not raw["records"].get(record):
            raw["records"].pop(record, None)

        if name:
            raw["_why"][voice] = why or raw["_why"].get(voice, "")
        else:
            raw["_why"].pop(voice, None)

        # ⚠️ Пишем через временный файл: словарь — единственный источник правды о правках, и
        # оборванная запись стоила бы всех имён разом.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return [record] if record else self.records_of(voice)

    def records_of(self, voice: str) -> list[str]:
        """Где звучит голос. Из снимка, а при его отсутствии — обходом расшифровок."""
        for v in self.data().get("voices") or []:
            if v["id"] == voice:
                return list(v.get("records") or [])
        return []
