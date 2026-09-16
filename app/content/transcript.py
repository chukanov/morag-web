"""Разбор тела расшифровки на реплики."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .frontmatter import read_document

# Формат строки: `[Имя] <!-- t:12.3 --> текст`.
# Проверено на корпусе: ловит 100% строк тела (54 выпуска, 4750 реплик, 0 исключений).
UTTERANCE_RE = re.compile(r"^\[([^\]]+)\] <!-- t:(\d+(?:\.\d+)?) --> (.*)$")


@dataclass(frozen=True)
class Utterance:
    sec: float
    end_sec: float
    speaker: str
    text: str

    def to_dict(self) -> dict:
        return {
            "sec": self.sec,
            "end_sec": self.end_sec,
            "speaker": self.speaker,
            "text": self.text,
        }


def parse_utterances(body: str, duration_sec: float = 0.0) -> list[Utterance]:
    """Конца реплики в данных нет — считаем его началом следующей (последняя тянется до конца выпуска)."""
    rows: list[tuple[float, str, str]] = []
    for line in body.splitlines():
        if not line.startswith("["):
            continue
        m = UTTERANCE_RE.match(line)
        if m:
            rows.append((float(m.group(2)), m.group(1), m.group(3)))

    out: list[Utterance] = []
    for i, (sec, speaker, text) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else max(duration_sec, sec)
        out.append(Utterance(sec=sec, end_sec=end, speaker=speaker, text=text))
    return out


@lru_cache(maxsize=8)
def _load_cached(path_str: str, mtime: float) -> tuple[Utterance, ...]:
    fm, body = read_document(Path(path_str))
    return tuple(parse_utterances(body, float(fm.get("duration_sec") or 0)))


def load_utterances(path: Path) -> list[Utterance]:
    """Тела по 100+ КБ — держим в кэше несколько последних (mtime в ключе = правки видны сразу)."""
    return list(_load_cached(str(path), path.stat().st_mtime))


def index_at(utterances: list[Utterance], sec: float) -> int:
    """Индекс реплики, звучащей на секунде `sec` (-1 до первой)."""
    idx = -1
    for i, u in enumerate(utterances):
        if u.sec <= sec:
            idx = i
        else:
            break
    return idx


def locate(utterances: list[Utterance], sec: float, tolerance: float = 2.0) -> int:
    """Индекс реплики, НАЧИНАЮЩЕЙСЯ на `sec`.

    Тайм-коды снаружи приходят целыми (`#t=2227`), а реплика стартует на 2227.4 —
    поэтому «последняя не позже» промахивается на одну назад. Допускаем сдвиг вперёд.
    """
    if not utterances:
        return -1
    idx = index_at(utterances, sec)
    nxt = idx + 1
    if nxt < len(utterances) and 0 <= utterances[nxt].sec - sec <= tolerance:
        return nxt
    return idx


def window(utterances: list[Utterance], sec: float, radius: int) -> list[Utterance]:
    """Окно ±radius реплик вокруг секунды — контекст для «вопроса от реплики»."""
    if not utterances:
        return []
    center = max(locate(utterances, sec), 0)
    lo = max(0, center - radius)
    hi = min(len(utterances), center + radius + 1)
    return utterances[lo:hi]
