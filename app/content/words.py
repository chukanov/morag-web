"""Пословные тайм-коды: чтение `record.words.json` рядом с расшифровкой.

Файл собирает `tools/make_record.py` из выравнивания адаптера транскрибации. Здесь его
только читаем: сервер ничего не считает, ему нужны готовые числа.

Формат (`morag-words-v1`): реплики, внутри слова тройками `[текст, начало, конец]`.
Слово хранится ВМЕСТЕ со своим временем, а не индексом в тексте: правка
расшифровки иначе молча сдвинула бы всю подсветку.
"""

from __future__ import annotations

import json
import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

FORMAT = "morag-words-v1"


@dataclass(frozen=True)
class WordTurn:
    start: float
    end: float
    speaker: str
    words: tuple[tuple[str, float, float], ...]
    # Идентификатор голоса из реестра: он общий на весь корпус и не меняется при переименовании.
    # Имя может повторяться у разных людей, метка после правки исчезает — а этот ключ остаётся.
    # Умолчание пустое: у старых файлов поля нет, и читалка обязана открыться и без него.
    speaker_id: str = ""

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "speaker": self.speaker,
            "speaker_id": self.speaker_id,
            "words": [list(w) for w in self.words],
        }


def words_path(transcript: Path) -> Path:
    """`…/ep5.md` → `…/ep5.words.json`."""
    return transcript.with_suffix(".words.json")


@lru_cache(maxsize=8)
def _load_cached(path_str: str, mtime: float) -> tuple[WordTurn, ...]:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        log.warning("незнакомый формат тайм-кодов в %s: %s", path_str, data.get("format"))
        return ()
    turns = []
    for turn in data.get("turns", []):
        words = tuple((str(w[0]), float(w[1]), float(w[2])) for w in turn.get("words", []) if len(w) >= 3)
        turns.append(
            WordTurn(
                start=float(turn.get("start") or 0),
                end=float(turn.get("end") or 0),
                speaker=str(turn.get("speaker") or ""),
                speaker_id=str(turn.get("speaker_id") or turn.get("speaker") or ""),
                words=words,
            )
        )
    return tuple(turns)


def load(transcript: Path) -> list[WordTurn]:
    """Слова выпуска. Файла нет — не ошибка: выпуск просто ещё не выровнен."""
    path = words_path(transcript)
    try:
        return list(_load_cached(str(path), path.stat().st_mtime))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError) as error:
        log.warning("не читаются тайм-коды %s: %s", path.name, error)
        return []


def context_between(turns: list[WordTurn], start: float, end: float, pad: int) -> list[WordTurn]:
    """Чанк плюс `pad` целых реплик до и после — «что вокруг».

    Края НЕ обрезаем, в отличие от `slice_between`: человек нажал «шире» именно
    затем, чтобы услышать мысль целиком, а обрезанная по секунде реплика
    начинается с середины слова говорящего — ровно та беда, от которой уходим.

    Сам чанк при этом остаётся узнаваемым: где он начался и кончился, клиент
    знает по `start`/`end`, которые он же и прислал.
    """
    if pad <= 0 or not turns:
        return slice_between(turns, start, end)
    hit = [i for i, t in enumerate(turns) if t.end >= start and t.start <= end]
    if not hit:
        return []
    lo = max(0, hit[0] - pad)
    hi = min(len(turns), hit[-1] + 1 + pad)
    return turns[lo:hi]


def slice_between(turns: list[WordTurn], start: float, end: float) -> list[WordTurn]:
    """Слова, звучащие в [start, end] — для карточки-момента.

    Реплика длиннее чанка, поэтому режем именно слова, а не реплики целиком:
    иначе карточка на 40 секунд тянула бы четырёхминутный монолог.
    """
    if end <= start:
        return []
    out: list[WordTurn] = []
    for turn in turns:
        if turn.end < start or turn.start > end:
            continue
        # слова упорядочены по времени — берём границы двоичным поиском
        starts = [w[1] for w in turn.words]
        lo = bisect_right(starts, start) - 1
        hi = bisect_left(starts, end)
        picked = turn.words[max(0, lo) : max(0, hi)]
        picked = tuple(w for w in picked if w[2] >= start and w[1] <= end)
        if picked:
            out.append(WordTurn(start=picked[0][1], end=picked[-1][2], speaker=turn.speaker,
                                speaker_id=turn.speaker_id, words=picked))
    return out
