"""Тесты пословных тайм-кодов: чтение файла и нарезка под чанк.

Работают на реальных `epN.words.json`, если они уже посчитаны, и на синтетике —
чтобы прогон был зелёным и на свежем клоне, где выравнивание ещё не гоняли.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.content import words as W  # noqa: E402

sys.path.insert(0, str(REPO / "tools"))
import spaces  # noqa: E402


def aligned_files() -> list[Path]:
    """Пословные времена по ВСЕМ пространствам; в чистой репе — по демо-корпусу.

    ⚠️ Список корней тут не выписывается руками: инструмент, знающий про один каталог из
    шести, молча проверил бы шестую часть корпуса и отрапортовал «чисто».
    """
    roots = [d for d in spaces.records_dirs() if d.is_dir()] or \
        [REPO / "corpora" / "demo" / "records"]
    return sorted(f for root in roots for f in root.glob("**/record.words.json"))


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    """Две реплики с известными временами — на них проверяем нарезку."""
    md = tmp_path / "ep1.md"
    md.write_text("---\ntitle: x\n---\n\n[Кто-то] <!-- t:0.0 --> раз два\n", encoding="utf-8")
    (tmp_path / "ep1.words.json").write_text(
        json.dumps(
            {
                "format": "morag-words-v1",
                "episode": "1-1",
                "duration_sec": 60,
                "turns": [
                    {
                        "start": 0.0,
                        "end": 20.0,
                        "speaker": "Первый",
                        "words": [["раз", 0.0, 1.0], ["два", 5.0, 6.0], ["три", 10.0, 11.0]],
                    },
                    {
                        "start": 20.0,
                        "end": 40.0,
                        "speaker": "Второй",
                        "words": [["четыре", 20.0, 21.0], ["пять", 30.0, 31.0]],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return md


# --- чтение ----------------------------------------------------------------


def test_missing_file_is_not_an_error(tmp_path: Path):
    """Выпуск может быть ещё не выровнен — это штатное состояние, не сбой."""
    assert W.load(tmp_path / "ep404.md") == []


def test_unknown_format_ignored(tmp_path: Path):
    md = tmp_path / "ep2.md"
    (tmp_path / "ep2.words.json").write_text('{"format":"чужое","turns":[{"words":[["a",0,1]]}]}', encoding="utf-8")
    assert W.load(md) == []


def test_broken_json_ignored(tmp_path: Path):
    md = tmp_path / "ep3.md"
    (tmp_path / "ep3.words.json").write_text("{не json", encoding="utf-8")
    assert W.load(md) == []


def test_load_reads_turns_and_words(sample: Path):
    turns = W.load(sample)
    assert [t.speaker for t in turns] == ["Первый", "Второй"]
    assert turns[0].words[0] == ("раз", 0.0, 1.0)
    assert sum(len(t.words) for t in turns) == 5


# --- нарезка под чанк ------------------------------------------------------


def test_slice_takes_only_words_in_range(sample: Path):
    """Реплика бывает длиннее чанка: режем слова, а не реплики целиком."""
    turns = W.load(sample)
    got = W.slice_between(turns, 4.0, 12.0)
    assert [w[0] for t in got for w in t.words] == ["два", "три"]


def test_slice_spans_turn_boundary(sample: Path):
    got = W.slice_between(W.load(sample), 9.0, 22.0)
    assert [t.speaker for t in got] == ["Первый", "Второй"]
    assert [w[0] for t in got for w in t.words] == ["три", "четыре"]


def test_slice_keeps_turn_meta_within_cut(sample: Path):
    """Границы отданной реплики — по оставшимся словам, иначе плеер соврёт про длину."""
    got = W.slice_between(W.load(sample), 4.0, 12.0)
    assert (got[0].start, got[0].end) == (5.0, 11.0)


def test_empty_and_inverted_ranges(sample: Path):
    turns = W.load(sample)
    assert W.slice_between(turns, 12.0, 4.0) == []
    assert W.slice_between(turns, 100.0, 200.0) == []


# --- контекст вокруг чанка -------------------------------------------------


def test_context_adds_whole_neighbour_turns(sample: Path):
    """«Шире» даёт соседние реплики ЦЕЛИКОМ — обрезок по секунде читать нельзя."""
    turns = W.load(sample)
    got = W.context_between(turns, 21.0, 22.0, pad=1)
    assert [t.speaker for t in got] == ["Первый", "Второй"]
    assert [w[0] for t in got for w in t.words] == ["раз", "два", "три", "четыре", "пять"]


def test_context_without_pad_is_plain_slice(sample: Path):
    turns = W.load(sample)
    assert W.context_between(turns, 4.0, 12.0, pad=0) == W.slice_between(turns, 4.0, 12.0)


def test_context_stops_at_episode_edges(sample: Path):
    """Просят шире, чем есть выпуск, — отдаём что есть, а не падаем."""
    got = W.context_between(W.load(sample), 0.0, 1.0, pad=99)
    assert [t.speaker for t in got] == ["Первый", "Второй"]


def test_context_outside_episode_is_empty(sample: Path):
    assert W.context_between(W.load(sample), 500.0, 600.0, pad=2) == []


# --- реальный корпус -------------------------------------------------------


def test_alignment_is_actually_checked():
    """Страховка от «зелёного на пустоте».

    Проверки ниже стоят под `skipif(not aligned_files())` — и это правильно для свежего клона,
    где выравнивание ещё не гоняли. Но тот же skip СКРЫВАЕТ поломку обхода: уедет раскладка на
    уровень глубже, список станет пустым, и весь файл молча перестанет что-либо проверять.
    Поэтому: если записи на диске есть, пословные времена обязаны найтись.
    """
    from app.content.records import RECORD_FILE
    roots = [d for d in spaces.records_dirs() if d.is_dir()] or [REPO / "corpora" / "demo" / "records"]
    records = [f for root in roots for f in root.glob(f"**/{RECORD_FILE}")]
    if records:
        assert aligned_files(), f"записей {len(records)}, а пословных времён не нашлось — сломан обход"


@pytest.mark.skipif(not aligned_files(), reason="выравнивание ещё не прогоняли")
def test_real_words_are_ordered_in_time():
    """Караоке ищет текущее слово двоичным поиском — на неотсортированном оно врёт молча."""
    for path in aligned_files():
        turns = W.load(path.with_suffix("").with_suffix(".md"))
        flat = [w for t in turns for w in t.words]
        assert flat, f"{path.name}: слов нет"
        bad = [(a, b) for a, b in zip(flat, flat[1:]) if b[1] < a[1]]
        assert not bad, f"{path.name}: порядок нарушен, напр. {bad[0]}"


@pytest.mark.skipif(not aligned_files(), reason="выравнивание ещё не прогоняли")
def test_real_words_fit_episode_duration():
    for path in aligned_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        duration = float(data["duration_sec"])
        last = max(w[2] for t in data["turns"] for w in t["words"] if t["words"])
        assert last <= duration + 5, f"{path.name}: слово за концом выпуска ({last} > {duration})"
