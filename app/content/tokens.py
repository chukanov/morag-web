"""Где ещё в корпусе встречается написание — и какие рядом варианты.

Нужно ровно для одного вопроса: человек поправил слово в реплике, и надо решить, чинить ли его
по всему корпусу правилом `text_fixes.json`. Без этого экрана правка вслепую: ⚠️ **гарбл никогда
не бывает один.** Замерено на живом корпусе: у одного термина шесть написаний, у другого
двенадцать, у фамилии докладчика десять — и правящий одно остальных попросту не видит.

Индекс не строим и на диск ничего не кладём: замерено на этом корпусе — чтение всех 167
расшифровок 71 мс, поиск токена 127 мс. Держим разбор в памяти и перечитываем по mtime.

⚠️ Слова режем ровно по той границе, по которой работает замена (`LEFT`/`RIGHT` в
`tools/make_record.py`): буквы, всё прочее — разделитель. Разойдись эти два представления —
и охват «встречается в N записях» врал бы ровно в тех случаях, ради которых его и смотрят.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

WORD_RE = re.compile(r"[^\W\d_]+")
RECORD_FILE = "record.md"

# Кириллица к латинице — чтобы «кафка» и «Kafka» оказались рядом. Таблица грубая и такой задумана:
# она обслуживает ПОДСКАЗКУ «посмотрите ещё на это», а не поиск и не канон.
TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "j", "з": "z",
    "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "c", "ш": "s", "щ": "s",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "u", "я": "a",
})


def fold(word: str) -> str:
    """Написание → грубый ключ для сравнения: без регистра и без алфавита."""
    return word.lower().translate(TRANSLIT)


def shares_stem(a: str, b: str) -> bool:
    """Общая основа — тот же признак, что склеивает написания имени в `tools/voices.py`.

    Не префикс: «Кузнецов» и «Кузнеч» расходятся на шестой букве, но это одна и та же фамилия,
    оборванная на слух.
    Четыре буквы — чтобы не слипались короткие разные слова; доля от короткого — чтобы длинное
    не притягивало к себе чужое с тем же началом.
    """
    x, y = fold(a), fold(b)
    if x == y:
        return True
    common = 0
    for p, q in zip(x, y):
        if p != q:
            break
        common += 1
    return common >= 4 and common >= 0.6 * min(len(x), len(y))


class Tokens:
    """Разбор корпуса на слова. Перечитывается, когда меняется любая расшифровка."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = [Path(r) for r in roots]
        self._by_word: dict[str, dict[str, int]] = {}
        self._stamp: float = -1.0

    def _files(self) -> list[Path]:
        # ⚠️ `**`, а не `*`: записи разложены по веткам и годам
        # (`records/<ветка>/<год>/<id>/record.md`) — путь и есть иерархия, которую видит
        # движок. Шаблон на один уровень нашёл бы НОЛЬ записей, и отказ был бы ТИХИМ:
        # в логе «0 шт.», на сайте пусто, ошибок нет.
        return sorted(f for root in self.roots for f in root.glob(f"**/{RECORD_FILE}"))

    def _build(self) -> None:
        files = self._files()
        stamp = max((f.stat().st_mtime for f in files), default=0.0)
        if stamp == self._stamp and self._by_word:
            return
        by_word: dict[str, dict[str, int]] = defaultdict(dict)
        for path in files:
            rid = path.parent.name
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:  # одна недочитанная запись не должна ронять весь экран
                log.exception("не прочитал %s", path)
                continue
            counts: dict[str, int] = defaultdict(int)
            for word in WORD_RE.findall(text):
                counts[word] += 1
            for word, n in counts.items():
                by_word[word][rid] = n
        self._by_word = dict(by_word)
        self._stamp = stamp

    def where(self, word: str) -> dict:
        """Точное написание: в каких записях и сколько раз."""
        self._build()
        hits = self._by_word.get(word) or {}
        return {
            "word": word,
            "records": [{"id": rid, "count": n} for rid, n in sorted(hits.items())],
            "total": sum(hits.values()),
        }

    def variants(self, word: str, limit: int = 20) -> list[dict]:
        """Близкие написания: то, что почти наверняка тот же гарбл в другой форме.

        Само слово в список входит — человеку надо видеть свой случай в ряду остальных, иначе
        непонятно, много это или мало.
        """
        self._build()
        out = []
        for other, hits in self._by_word.items():
            if not shares_stem(word, other):
                continue
            out.append({"word": other, "records": len(hits), "total": sum(hits.values())})
        # Сначала частое: редкая форма — это обычно опечатка расшифровки, и она внизу списка.
        out.sort(key=lambda v: (-v["total"], v["word"]))
        return out[:limit]
