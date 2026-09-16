"""Правка реплики с сайта: пересчёт времён, применение и обратный путь до сырца.

Самое опасное здесь — времена. Ошибка в них не роняет сборку: караоке просто начнёт
подсвечивать не то слово, и увидят это через месяц. Поэтому проверок на времена больше,
чем на всё остальное вместе.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import turn_edits  # noqa: E402


def words(*items) -> list[list]:
    return [list(i) for i in items]


# --- пересчёт времён --------------------------------------------------------

def test_untouched_words_keep_their_time_byte_for_byte():
    """Слово, которого правка не коснулась, не пересчитывается вовсе.

    Это главное обещание всей затеи: время у него ИЗМЕРЕНО по звуку, и любая арифметика над
    ним — потеря. Правка одного слова не имеет права шевелить соседей.
    """
    old = words(("Мы", 1.0, 1.2), ("ставим", 1.3, 1.9), ("Grafanna", 2.0, 2.6), ("в", 2.7, 2.8))
    out = turn_edits.retime(old, ["Мы", "ставим", "Grafana", "в"], (0.0, 5.0))
    assert [w[0] for w in out] == ["Мы", "ставим", "Grafana", "в"]
    assert out[0] == ["Мы", 1.0, 1.2]
    assert out[1] == ["ставим", 1.3, 1.9]
    assert out[3] == ["в", 2.7, 2.8]
    # Заменённое берёт промежуток того, кого заменило: слово звучало ровно там.
    assert out[2][1] == pytest.approx(2.0)
    assert out[2][2] == pytest.approx(2.6)


def test_deleted_word_leaves_an_honest_hole():
    """Удалённое слово исчезает, а его время НЕ раздаётся соседям.

    Растянуть соседей на освободившийся промежуток значило бы соврать: в звуке они там не
    звучат. Дыра во времени у нас честная — на том же принципе стоит `find_gaps.py`.
    """
    old = words(("это", 1.0, 1.2), ("ээ", 1.3, 1.6), ("важно", 1.7, 2.4))
    out = turn_edits.retime(old, ["это", "важно"], (0.0, 5.0))
    assert out == [["это", 1.0, 1.2], ["важно", 1.7, 2.4]]


def test_inserted_word_takes_the_pause_between_neighbours():
    old = words(("мы", 1.0, 1.2), ("ставим", 3.0, 3.6))
    out = turn_edits.retime(old, ["мы", "уже", "ставим"], (0.0, 5.0))
    assert [w[0] for w in out] == ["мы", "уже", "ставим"]
    assert out[0] == ["мы", 1.0, 1.2] and out[2] == ["ставим", 3.0, 3.6]
    assert 1.2 <= out[1][1] and out[1][2] <= 3.0, "вставленное слово вышло за паузу"


def test_insert_without_a_pause_keeps_the_order():
    """Паузы нет вовсе — слова получают нулевую ширину, но порядок НЕ ломается.

    ⚠️ Караоке ищет текущее слово двоичным поиском (`indexAt` в web/js/ui/karaoke.js) и на
    убывающем времени врёт молча. Лучше слово нулевой длительности, чем сдвинутое назад.
    """
    old = words(("раз", 1.0, 1.5), ("два", 1.5, 2.0))
    out = turn_edits.retime(old, ["раз", "и", "ещё", "два"], (0.0, 5.0))
    starts = [w[1] for w in out]
    assert starts == sorted(starts)


@pytest.mark.parametrize("now", [
    ["Мы", "ставим", "Grafana", "в", "прод"],           # вставка в хвост
    ["Сегодня", "мы", "ставим", "Grafanna", "в"],        # вставка в голову
    ["Grafanna", "в"],                                   # выкинули начало
    ["Мы"],                                           # выкинули всё, кроме первого
    ["Совсем", "другой", "текст", "реплики", "тут"],  # переписали целиком
])
def test_times_never_go_backwards(now):
    old = words(("Мы", 1.0, 1.2), ("ставим", 1.3, 1.9), ("Grafanna", 2.0, 2.6), ("в", 2.7, 2.8))
    out = turn_edits.retime(old, now, (0.5, 4.0))
    starts = [w[1] for w in out]
    assert starts == sorted(starts), f"порядок сломан: {out}"
    assert all(w[2] >= w[1] for w in out), f"слово кончается раньше, чем начинается: {out}"


# --- поиск куска ------------------------------------------------------------

def test_repeated_fragment_resolved_by_hint():
    """Один и тот же кусок дважды в реплике — развязываем подсказкой, а не первым попавшимся."""
    line = "да да ладно я понял да да".split()
    assert turn_edits._find(line, ["да", "да"], at=None) == 0
    assert turn_edits._find(line, ["да", "да"], at=5) == 5


def test_missing_fragment_is_loud():
    with pytest.raises(turn_edits.EditError):
        turn_edits._find("совсем другой текст".split(), ["нет", "такого"], at=None)


# --- применение к артефакту -------------------------------------------------

def artifact() -> dict:
    body = ("[Пётр Ковалёв] <!-- t:1.0 --> Мы ставим Grafanna в прод.\n\n"
            "[Speaker_3] <!-- t:9.0 --> А почему не Kafka?\n")
    return {
        "markdown": "---\ntitle: \"x\"\n---\n\n" + body,
        "turns": [
            {"speaker": "Пётр Ковалёв", "text": "Мы ставим Grafanna в прод.",
             "raw": "Мы ставим Grafanna в прод."},
            {"speaker": "Speaker_3", "text": "А почему не Kafka?", "raw": "А почему не Kafka?"},
        ],
        "words": {"format": "morag-words-v1", "turns": [
            {"start": 1.0, "end": 3.4, "speaker": "Пётр Ковалёв", "words": words(
                ("Мы", 1.0, 1.2), ("ставим", 1.3, 1.9), ("Grafanna", 2.0, 2.6),
                ("в", 2.7, 2.8), ("прод.", 2.9, 3.4))},
            {"start": 9.0, "end": 10.2, "speaker": "Speaker_3", "words": words(
                ("А", 9.0, 9.2), ("почему", 9.3, 9.7), ("не", 9.8, 9.9), ("Kafka?", 10.0, 10.2))},
        ]},
    }


def test_edit_touches_all_three_places_at_once():
    """Тело, реплика и пословные времена обязаны остаться согласованными.

    ⚠️ Разойдись они — `split_long_turns` молча перестанет резать реплику на абзацы: он сверяет
    `[w[0] for w in words]` с `text.split()` и при расхождении просто пропускает реплику.
    """
    x = artifact()
    turn_edits.apply(x, [{"turn": 0, "was": "Grafanna в прод.", "now": "Grafana в прод."}], warn=print)
    assert "Grafana в прод." in x["markdown"]
    assert "Grafanna" not in x["markdown"]
    assert x["turns"][0]["text"] == "Мы ставим Grafana в прод."
    assert [w[0] for w in x["words"]["turns"][0]["words"]] == x["turns"][0]["text"].split()
    # Метка времени абзаца остаётся на месте: правили слова, а не начало реплики.
    assert "<!-- t:1.0 -->" in x["markdown"]


def test_raw_stays_raw():
    """`turns[].raw` — то, что расслышал ASR. По нему ищут самопредставления (`tools/voices.py`),
    и правка написания не имеет права его переписывать."""
    x = artifact()
    turn_edits.apply(x, [{"turn": 0, "was": "Grafanna", "now": "Grafana"}], warn=print)
    assert x["turns"][0]["raw"] == "Мы ставим Grafanna в прод."


def test_turn_bounds_follow_the_words():
    x = artifact()
    turn_edits.apply(x, [{"turn": 1, "was": "А почему", "now": "Почему"}], warn=print)
    turn = x["words"]["turns"][1]
    assert turn["start"] == turn["words"][0][1]
    assert turn["end"] == turn["words"][-1][2]


def test_unknown_fragment_is_skipped_loudly_and_changes_nothing():
    """Запись переснимут — границы реплик поедут. Правка обязана ОТКАЗАТЬСЯ применяться."""
    x = artifact()
    said = []
    applied = turn_edits.apply(x, [{"turn": 0, "was": "такого тут нет", "now": "неважно"}],
                               warn=said.append)
    assert applied == []
    assert "Мы ставим Grafanna в прод." == x["turns"][0]["text"]
    assert said and "не применена" in said[0]


def test_empty_replacement_is_refused():
    """Пустая реплика — это реплика без слов и без границ, караоке на ней спотыкается."""
    x = artifact()
    said = []
    assert turn_edits.apply(x, [{"turn": 0, "was": "Мы ставим Grafanna в прод.", "now": "  "}],
                            warn=said.append) == []
    assert said and "пустая замена" in said[0]


def test_edits_chain_in_order():
    """Вторая правка видит текст ПОСЛЕ первой — иначе цепочка правок одного места развалилась бы."""
    x = artifact()
    applied = turn_edits.apply(x, [
        {"turn": 0, "was": "Grafanna", "now": "Grafana"},
        {"turn": 0, "was": "ставим Grafana", "now": "ставим Grafana 2"},
    ], warn=print)
    assert len(applied) == 2
    assert x["turns"][0]["text"] == "Мы ставим Grafana 2 в прод."


# --- обратный путь: адрес абзаца -------------------------------------------

def test_addresses_survive_the_build_and_point_at_the_raw_turn():
    """Адрес слова обязан пережить гарблы, цензуру и разрез на абзацы.

    ⚠️ Держится это на том, что `substitute` собирает слово как `[..., *w[1:]]`, а
    `paragraphs_of` перекладывает слово целиком. Разъедется — правка уедет в чужое место.
    """
    from make_record import apply_text_fixes, split_long_turns

    x = artifact()
    turn_edits.stamp(x)
    apply_text_fixes(x, [{"was": "Grafanna", "now": "Grafana"}])
    split_long_turns(x)
    got = turn_edits.slices(x)
    assert got[0] == {"turn": 0, "from": 0, "to": 4}
    assert got[1] == {"turn": 1, "from": 0, "to": 3}
    assert [w[0] for w in x["words"]["turns"][0]["words"]][2] == "Grafana"


def test_collapsed_repeat_does_not_shift_the_address():
    """Схлопнутый повтор УДАЛЯЕТ слова — «номер абзаца плюс смещение» после него врёт.

    Замерено на курсе QA: 1758 слов мусора в 25 записях. Поэтому адрес несём при слове, а не
    считаем по порядку.
    """
    from make_record import drop_degenerate

    x = artifact()
    x["markdown"] = ("---\ntitle: \"x\"\n---\n\n"
                     "[Пётр Ковалёв] <!-- t:1.0 --> да да да да да потом важное слово.\n")
    x["turns"] = [{"speaker": "Пётр Ковалёв", "text": "да да да да да потом важное слово."}]
    x["words"]["turns"] = [{"start": 1.0, "end": 4.0, "speaker": "Пётр Ковалёв", "words": words(
        ("да", 1.0, 1.1), ("да", 1.2, 1.3), ("да", 1.4, 1.5), ("да", 1.6, 1.7),
        ("да", 1.8, 1.9), ("потом", 2.0, 2.4), ("важное", 2.5, 3.0), ("слово.", 3.1, 4.0))}]
    turn_edits.stamp(x)
    drop_degenerate(x)
    left = x["words"]["turns"][0]["words"]
    assert [w[0] for w in left] == ["да", "потом", "важное", "слово."]
    # Четвёртое СЛОВО абзаца — «слово.», а в сырце оно восьмое. Наивный счёт дал бы 3.
    assert left[-1][turn_edits.ADDR] == [0, 7]
    assert turn_edits.slices(x)[0] == {"turn": 0, "from": 0, "to": 7}


def test_unstamp_returns_plain_triples():
    """На диск слово обязано уехать тройкой: формат `morag-words-v1` читают и фронт, и морагом."""
    x = artifact()
    turn_edits.stamp(x)
    turn_edits.unstamp(x)
    assert all(len(w) == 3 for t in x["words"]["turns"] for w in t["words"])


# --- сквозь всю сборку ------------------------------------------------------
#
# ⚠️ Артефакт здесь СВОЙ, а не из `test_make_record`: там текст реплик намеренно не сходится
# с пословными временами (проверяется другое), а правка реплики на таком артефакте честно
# отказывается работать — согласованность текста и слов и есть её условие.

CLI = REPO / "tools" / "make_record.py"


def run(*args: str):
    import subprocess

    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True)


def whole_artifact() -> dict:
    body = ("[Пётр Ковалёв] <!-- t:1.0 --> Мы ставим Grafanna в прод.\n\n"
            "[Speaker_3] <!-- t:9.0 --> А почему не Kafka?\n")
    x = artifact()
    x["markdown"] = '---\ntitle: "x"\nurl: file:///tmp/x.mp4\n---\n\n' + body
    x["coverage"] = {"audio_sec": 610.0}
    x["speaker_names"] = {}
    return {"x_enriched": x}


def make_corpus(tmp_path: Path, edits: list[dict], fixes: dict | None = None) -> Path:
    root = tmp_path / "corp"
    (root / "inbox").mkdir(parents=True)
    (root / "records").mkdir()
    (root / "inbox" / "Планёрка.json").write_text(
        json.dumps(whole_artifact(), ensure_ascii=False), encoding="utf-8")
    (root / "names.json").write_text(json.dumps({"speakers": {}, "records": {}}), encoding="utf-8")
    (root / "text_fixes.json").write_text(
        json.dumps(fixes or {"global": [], "records": {}}, ensure_ascii=False), encoding="utf-8")
    (root / "turn_fixes.json").write_text(
        json.dumps({"records": {"2026-03-12-grafana": edits}}, ensure_ascii=False), encoding="utf-8")
    return root


def build(root: Path) -> Path:
    out = run(str(root / "inbox" / "Планёрка.json"), "--id", "2026-03-12-grafana",
              "--title", "Grafana без боли", "--no-media")
    assert out.returncode == 0, out.stderr + out.stdout
    return root / "records" / "2026-03-12-grafana"


def test_edit_reaches_the_record_and_is_reversible(tmp_path):
    """Правка доезжает до `record.md` и до пословных времён, а снятие её ОТМЕНЯЕТ.

    Обратимость — это тест, а не обещание: сайдкар остаётся сырым, и текст всегда выводится
    из словарей. На том же держатся имена и гарблы.
    """
    root = make_corpus(tmp_path, [{"turn": 0, "was": "Grafanna", "now": "Grafana"}])
    record = build(root)
    assert "Мы ставим Grafana в прод." in (record / "record.md").read_text(encoding="utf-8")
    left = json.loads((record / "record.words.json").read_text(encoding="utf-8"))
    assert [w[0] for w in left["turns"][0]["words"]] == "Мы ставим Grafana в прод.".split()
    assert all(len(w) == 3 for t in left["turns"] for w in t["words"]), "адрес уехал на диск"
    # Заменённое слово взяло время того, кого заменило: соседей никто не двигал.
    assert left["turns"][0]["words"][2][1] == 2.0

    (root / "turn_fixes.json").write_text(json.dumps({"records": {}}), encoding="utf-8")
    out = run(str(record))
    assert out.returncode == 0, out.stderr + out.stdout
    assert "Мы ставим Grafanna в прод." in (record / "record.md").read_text(encoding="utf-8")


def test_edit_runs_before_the_dictionaries(tmp_path):
    """Правило, заведённое ПОЗЖЕ, чинит и правленую реплику.

    Обратный порядок заморозил бы реплику целиком: заведённый завтра гарбл до неё бы не дошёл,
    и человек, поправивший одно слово, потерял бы все будущие правки этого места.
    """
    root = make_corpus(tmp_path,
                       [{"turn": 0, "was": "в прод.", "now": "в прод и в Кафкв."}],
                       fixes={"global": [{"was": "Кафкв.", "now": "Kafka."}], "records": {}})
    body = (build(root) / "record.md").read_text(encoding="utf-8")
    assert "в прод и в Kafka." in body, "гарбл не дошёл до правленой реплики"


def test_multiword_replacement_in_the_dictionary_is_refused(tmp_path):
    """Замена с пробелом отвергается и у гарблов, а не только у цензуры.

    ⚠️ «Слово» с пробелом в пословных временах молча отключает разрез на абзацы:
    `split_long_turns` сверяет слова с текстом и при расхождении просто пропускает реплику.
    Часовой доклад стал бы одной простынёй с одним тайм-кодом, без ошибки в логе.
    """
    root = make_corpus(tmp_path, [], fixes={"global": [{"was": "Grafanna", "now": "Grafana Metrics"}],
                                            "records": {}})
    out = run(str(root / "inbox" / "Планёрка.json"), "--id", "2026-03-12-grafana", "--title", "x")
    assert out.returncode != 0
    assert "не одно слово" in out.stdout + out.stderr


def test_word_boundary_holds(tmp_path):
    """Граница слова: правило на `Кафка` не имеет права сработать внутри `Кафкианский`.

    В словаре корпуса такие пары отмечены предупреждением — короткое написание живёт внутри
    длинного и законного. Пусть предупреждение будет и тестом.
    """
    from make_record import LEFT, RIGHT, apply_text_fixes
    import re

    x = artifact()
    x["turns"][0]["text"] = "Кафкианский и Кафка"
    apply_text_fixes(x, [{"was": "Кафка", "now": "Kafka"}])
    assert re.search(rf"{LEFT}Кафка{RIGHT}", "Кафкианский") is None
    assert x["turns"][0]["text"] == "Кафкианский и Kafka"
