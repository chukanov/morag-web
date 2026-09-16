"""Сборка записи из артефакта транскрибации.

Проверяем на синтетическом артефакте той же формы, что отдаёт адаптер: `x_enriched` с
`markdown`, `turns`, `words`, `coverage`, `speaker_names`. Настоящие артефакты весят десятки
мегабайт, в git им не место, а форма важнее объёма.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "make_record.py"

BODY = (
    "[Пётр Ковалёв] <!-- t:1.5 --> Всем привет, я расскажу про Кафку.\n\n"
    "[Speaker_3] <!-- t:20.0 --> А вопрос из зала можно?\n\n"
    "[Пётр Ковалёв] <!-- t:25.0 --> Кафку мы выбрали не сразу.\n"
)

ARTIFACT = {
    "x_enriched": {
        # ⚠️ У настоящего артефакта шапка вырожденная: клиент знает только имя файла.
        # Именно поэтому шапку записи собирает make_record, а не копирует.
        "markdown": '---\ntitle: "Планёрка 12 марта"\nurl: file:///tmp/x.mp4\n---\n\n' + BODY,
        "turns": [
            {"speaker": "Пётр Ковалёв", "start": 1.5, "text": "Всем привет, я расскажу про Кафку."},
            {"speaker": "Speaker_3", "start": 20.0, "text": "А вопрос из зала можно?"},
            {"speaker": "Пётр Ковалёв", "start": 25.0, "text": "Кафку мы выбрали не сразу."},
        ],
        "speaker_names": {"Speaker_0": "Пётр Ковалёв"},
        "coverage": {"audio_sec": 1830.4},
        "words": {
            "format": "morag-words-v1",
            "duration_sec": 1830.4,
            "turns": [
                {"speaker": "Пётр Ковалёв", "words": [["Всем", 1.5, 1.8], ["привет,", 1.9, 2.3],
                                                     ["Кафку.", 2.4, 2.9]]},
                {"speaker": "Speaker_3", "words": [["А", 20.0, 20.1], ["вопрос", 20.2, 20.6]]},
            ],
        },
    }
}


def corpus(tmp_path: Path, names: dict | None = None, fixes: dict | None = None) -> Path:
    """Каталог корпуса с артефактом в inbox — как после прогона transcribe.sh."""
    root = tmp_path / "corp"
    (root / "inbox").mkdir(parents=True)
    (root / "records").mkdir()
    (root / "media").mkdir()
    (root / "inbox" / "Планёрка 12 марта.json").write_text(
        json.dumps(ARTIFACT, ensure_ascii=False), encoding="utf-8")
    (root / "names.json").write_text(json.dumps(names or {"speakers": {}, "records": {}},
                                                ensure_ascii=False), encoding="utf-8")
    (root / "text_fixes.json").write_text(json.dumps(fixes or {"global": [], "records": {}},
                                                     ensure_ascii=False), encoding="utf-8")
    return root


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


def build(root: Path, *extra: str) -> Path:
    out = run(str(root / "inbox" / "Планёрка 12 марта.json"), "--id", "2026-03-12-kafka",
              "--title", "Kafka без боли", "--event", "Backend-митап #17", *extra)
    assert out.returncode == 0, out.stderr + out.stdout
    return root / "records" / "2026-03-12-kafka"


def head_of(record: Path) -> dict:
    text = (record / "record.md").read_text(encoding="utf-8")
    head = text.split("---", 2)[1]
    return {k.strip(): json.loads(v.strip())
            for k, _, v in (line.partition(":") for line in head.strip().splitlines())}


def people_of(head: dict) -> list[str]:
    """Все названные люди шапки, независимо от роли. У тестового корпуса нет `hub.yml`, значит
    модель ролей — `meeting`, и названные голоса лежат в `participants`."""
    return list(head.get("speakers", [])) + list(head.get("participants", []))


def test_head_builds_from_flags_not_from_artifact(tmp_path):
    """Вырожденный заголовок артефакта не должен доехать до записи: он кормит `catalog`."""
    record = build(corpus(tmp_path))
    head = head_of(record)
    assert head["title"] == "Kafka без боли"
    assert head["date"] == "2026-03-12"
    assert head["event"] == "Backend-митап #17"
    assert "url" not in head


def test_speakers_ordered_and_unnamed_dropped(tmp_path):
    """Порядок первого появления; разовый голос из зала в шапку не идёт, но считается в `voices`."""
    head = head_of(build(corpus(tmp_path)))
    assert people_of(head) == ["Пётр Ковалёв"]
    assert head["voices"] == 2


def test_duration_from_coverage(tmp_path):
    assert head_of(build(corpus(tmp_path)))["duration_sec"] == 1830


def test_media_moved_and_named_by_record(tmp_path):
    root = corpus(tmp_path)
    (root / "inbox" / "Планёрка 12 марта.mp4").write_bytes(b"video")
    record = build(root)
    assert head_of(record)["media"] == "2026-03-12-kafka.mp4"
    assert (root / "media" / "2026-03-12-kafka.mp4").is_file()
    assert not (root / "inbox" / "Планёрка 12 марта.mp4").exists()


def test_without_media_record_still_builds(tmp_path):
    """Перенесённый с прежнего сайта анонс без видео — тоже контент, а не ошибка."""
    record = build(corpus(tmp_path))
    assert "media" not in head_of(record)
    assert (record / "record.md").is_file()


def test_date_is_required(tmp_path):
    """Дата уезжает в каждый чанк и кормит каталожные вопросы — без неё сборки нет."""
    root = corpus(tmp_path)
    out = run(str(root / "inbox" / "Планёрка 12 марта.json"), "--id", "kafka-bez-boli")
    assert out.returncode != 0
    assert "date" in (out.stderr + out.stdout)


def test_date_derived_from_id(tmp_path):
    root = corpus(tmp_path)
    out = run(str(root / "inbox" / "Планёрка 12 марта.json"), "--id", "2026-03-12-kafka")
    assert out.returncode == 0, out.stderr
    assert head_of(root / "records" / "2026-03-12-kafka")["date"] == "2026-03-12"


def test_text_fixes_reach_body_turns_and_words(tmp_path):
    """Правка обязана дойти до всех трёх мест, иначе расшифровка и караоке разъедутся."""
    root = corpus(tmp_path, fixes={"global": [{"was": "Кафку", "now": "Kafka"}], "records": {}})
    record = build(root)
    assert "Кафку" not in (record / "record.md").read_text(encoding="utf-8")
    words = json.loads((record / "record.words.json").read_text(encoding="utf-8"))
    assert all("Кафку" not in w[0] for t in words["turns"] for w in t["words"])


def test_sidecar_stays_raw(tmp_path):
    """Сайдкар — СЫРОЙ вывод ASR, правки в него не запекаются.

    Иначе правку нельзя было бы отменить: убрал запись из словаря — а текст уже испорчен
    навсегда, потому что пересобирать не из чего. Единственный источник правды о правках —
    словари; record.md из них выводится.
    """
    root = corpus(tmp_path, fixes={"global": [{"was": "Кафку", "now": "Kafka"}], "records": {}})
    record = build(root)
    sidecar = json.loads((record / "record.json").read_text(encoding="utf-8"))["x_enriched"]
    assert "Кафку" in sidecar["markdown"]
    assert any("Кафку" in (t.get("text") or "") for t in sidecar["turns"])


def test_removing_a_fix_undoes_it(tmp_path):
    """Обратная сторона сырого сайдкара — ради которой он и сырой."""
    root = corpus(tmp_path, fixes={"global": [{"was": "Кафку", "now": "Kafka"}], "records": {}})
    record = build(root)
    assert "Кафку" not in (record / "record.md").read_text(encoding="utf-8")
    (root / "text_fixes.json").write_text(json.dumps({"global": [], "records": {}}),
                                          encoding="utf-8")
    assert run(str(record)).returncode == 0
    assert "Кафку" in (record / "record.md").read_text(encoding="utf-8")


def test_word_replacement_keeps_timings(tmp_path):
    """Замена идёт один-в-один по токенам: перевыравнивание не нужно."""
    root = corpus(tmp_path, fixes={"global": [{"was": "Кафку.", "now": "Kafka."}], "records": {}})
    words = json.loads((build(root) / "record.words.json").read_text(encoding="utf-8"))
    assert words["turns"][0]["words"][2] == ["Kafka.", 2.4, 2.9]


def test_fix_does_not_break_word_boundary(tmp_path):
    """`Кафк` не должен срабатывать внутри `Кафку` — иначе словарь начнёт портить текст."""
    root = corpus(tmp_path, fixes={"global": [{"was": "Кафк", "now": "XXX"}], "records": {}})
    assert "XXX" not in (build(root) / "record.md").read_text(encoding="utf-8")


def test_name_override_applied_everywhere(tmp_path):
    root = corpus(tmp_path, names={"speakers": {"Speaker_0": "П. Ковалёв"}, "records": {}})
    record = build(root)
    text = (record / "record.md").read_text(encoding="utf-8")
    assert "[П. Ковалёв]" in text and "[Пётр Ковалёв]" not in text
    assert people_of(head_of(record)) == ["П. Ковалёв"]
    words = json.loads((record / "record.words.json").read_text(encoding="utf-8"))
    assert words["turns"][0]["speaker"] == "П. Ковалёв"


def test_rebuild_without_changes_does_not_touch_file(tmp_path):
    """Индексатор решает «изменился ли документ» по mtime: лишняя запись = переиндексация корпуса."""
    record = build(corpus(tmp_path))
    before = (record / "record.md").stat().st_mtime_ns
    out = run(str(record))
    assert out.returncode == 0, out.stderr
    assert (record / "record.md").stat().st_mtime_ns == before
    assert "изменений нет" in out.stdout


def test_rebuild_applies_new_fixes_and_keeps_head(tmp_path):
    """Правку кладут в словарь, а не в record.md: пересборка иначе молча её откатит."""
    root = corpus(tmp_path)
    record = build(root)
    (root / "text_fixes.json").write_text(
        json.dumps({"global": [{"was": "Кафку", "now": "Kafka"}], "records": {}},
                   ensure_ascii=False), encoding="utf-8")
    out = run(str(record))
    assert out.returncode == 0, out.stderr
    assert "Кафку" not in (record / "record.md").read_text(encoding="utf-8")
    assert head_of(record)["event"] == "Backend-митап #17"


def test_unsorted_words_are_reported(tmp_path):
    """Караоке ищет слово двоичным поиском и на неотсортированном врёт молча."""
    root = corpus(tmp_path)
    path = root / "inbox" / "Планёрка 12 марта.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["x_enriched"]["words"]["turns"][0]["words"] = [["раз", 9.0, 9.5], ["два", 1.0, 1.5]]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = run(str(path), "--id", "2026-03-12-kafka", "--title", "t", "--event", "e")
    assert out.returncode == 0, out.stderr
    assert "раньше предыдущего" in out.stdout


def test_not_an_artifact_fails_clearly(tmp_path):
    (tmp_path / "junk.json").write_text('{"hello": 1}', encoding="utf-8")
    out = run(str(tmp_path / "junk.json"), "--id", "2026-01-01-x")
    assert out.returncode != 0
    assert "x_enriched" in (out.stderr + out.stdout)


def test_no_word_timings_writes_no_words_file(tmp_path):
    """Без выравнивания файла пословных времён быть НЕ должно.

    Караоке решает «умеем ли мы это для записи» по наличию файла: пустышка — молчаливый обман,
    сайт покажет управление, которое ничего не делает.
    """
    root = corpus(tmp_path)
    path = root / "inbox" / "Планёрка 12 марта.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["x_enriched"].pop("words")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = run(str(path), "--id", "2026-03-12-kafka", "--title", "t", "--event", "e")
    assert out.returncode == 0, out.stderr
    record = root / "records" / "2026-03-12-kafka"
    assert (record / "record.md").is_file()
    assert not (record / "record.words.json").exists()
    assert "тайм-кодов нет" in out.stdout


def test_stale_words_file_is_reported(tmp_path):
    """Пересборка без времён поверх записи, где они были, обязана предупредить: текст разъедется."""
    record = build(corpus(tmp_path))
    assert (record / "record.words.json").is_file()
    sidecar = record / "record.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["x_enriched"].pop("words")
    sidecar.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = run(str(record))
    assert out.returncode == 0, out.stderr
    assert "протух" in out.stdout


def test_name_assigned_when_autonaming_was_off(tmp_path):
    """Основной наш режим: наминг выключен, speaker_names пуст, метка в теле = Speaker_N.

    Ранняя версия умела только ИСПРАВЛЯТЬ уже поставленное имя и в этом случае молча
    не делала ничего — запись уезжала в корпус без спикеров вовсе.
    """
    root = corpus(tmp_path, names={"speakers": {"Speaker_3": "Пётр Смирнов"}, "records": {}})
    path = root / "inbox" / "Планёрка 12 марта.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["x_enriched"]["speaker_names"] = {}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = run(str(path), "--id", "2026-03-12-kafka", "--title", "t", "--event", "e")
    assert out.returncode == 0, out.stderr
    record = root / "records" / "2026-03-12-kafka"
    assert "[Пётр Смирнов]" in (record / "record.md").read_text(encoding="utf-8")
    assert "Пётр Смирнов" in people_of(head_of(record))


def test_name_for_absent_speaker_is_not_reported(tmp_path):
    """Словарь общий на корпус: человека, которого в этой записи нет, не считаем применённой правкой."""
    root = corpus(tmp_path, names={"speakers": {"Speaker_42": "Кто-то Другой"}, "records": {}})
    out = run(str(root / "inbox" / "Планёрка 12 марта.json"), "--id", "2026-03-12-kafka",
              "--title", "t", "--event", "e")
    assert out.returncode == 0, out.stderr
    assert "наминг поправлен" not in out.stdout


# --- разрез длинных реплик на абзацы -----------------------------------------
# Тайм-код в расшифровке стоит ОДИН на реплику, и чанкер морага, разрезая монолог, вычисляет
# время внутри него интерполяцией по позиции в тексте. Замерено на корпусе: медиана ошибки
# 2-57 секунд, худшая 110 — клик по цитате открывал бы читалку в двух минутах от нужного места.
# Абзац со своей МЕРЕНОЙ меткой это снимает, и движку не нужно знать ни про какой сайдкар.

sys.path.insert(0, str(TOOL.parent))
import make_record  # noqa: E402
from make_record import paragraphs_of, split_long_turns  # noqa: E402


def говорит(start: float, n: int, step: float = 0.4, pause_at: int | None = None,
            pause: float = 3.0) -> list:
    """n слов подряд; если задано, после слова pause_at — пауза."""
    out, t = [], start
    for i in range(n):
        out.append([f"слово{i}", round(t, 2), round(t + step * 0.8, 2)])
        t += step + (pause if pause_at == i else 0)
    return out


def test_paragraph_boundary_lands_on_the_longest_pause():
    # 200 слов по 0.4 с — 80 секунд речи; единственная длинная пауза после 120-го слова
    words = говорит(0.0, 200, pause_at=119, pause=4.0)

    groups = paragraphs_of(words, target=45.0)

    assert len(groups) == 2
    assert groups[0][-1][0] == "слово119", "разрез ушёл не в паузу"
    assert groups[1][0][0] == "слово120"


def test_short_turn_is_not_split():
    assert len(paragraphs_of(говорит(0.0, 40), target=45.0)) == 1


def test_split_rewrites_text_and_times_together():
    """Караоке строится из времён, а текст читается из markdown: разъехаться им нельзя."""
    words = говорит(10.0, 200, pause_at=119, pause=4.0)
    x = {
        "markdown": "---\ntitle: x\n---\n\n[Аня] <!-- t:10.0 --> " + " ".join(w[0] for w in words) + "\n",
        "words": {"turns": [{"start": 10.0, "end": words[-1][2], "speaker": "Аня", "words": words}]},
    }

    cuts = split_long_turns(x, target=45.0)

    assert cuts == 1
    lines = [ln.strip() for ln in x["markdown"].split("\n\n") if ln.startswith("[")]
    assert len(lines) == 2 and len(x["words"]["turns"]) == 2, "текст и времена разрезаны по-разному"
    # метка второго абзаца — НАСТОЯЩЕЕ время его первого слова, а не доля от длины реплики
    assert lines[1].startswith(f"[Аня] <!-- t:{words[120][1]:.1f} -->")
    assert x["words"]["turns"][1]["start"] == pytest.approx(words[120][1])
    assert lines[0].endswith("слово119") and lines[1].endswith(f"слово{len(words) - 1}")


def test_without_word_times_nothing_is_split():
    """Без пословных времён честной метки не поставить, а выдуманная хуже отсутствующей."""
    x = {"markdown": "---\ntitle: x\n---\n\n[Аня] <!-- t:0.0 --> " + "слово " * 500 + "\n", "words": {}}

    assert split_long_turns(x) == 0


def test_text_and_times_out_of_sync_are_left_alone():
    """Словарь правок мог изменить число токенов — тогда резать нечем, и это не ошибка."""
    words = говорит(0.0, 200, pause_at=119)
    x = {
        "markdown": "---\ntitle: x\n---\n\n[Аня] <!-- t:0.0 --> совсем другой текст\n",
        "words": {"turns": [{"start": 0.0, "end": 100.0, "speaker": "Аня", "words": words}]},
    }

    assert split_long_turns(x, target=45.0) == 0
    assert len(x["words"]["turns"]) == 1


# --- сочинения на тишине ------------------------------------------------------

def test_degenerate_run_collapses_to_one_occurrence():
    """Whisper на тишине не молчит, а повторяет. Схлопываем до одного вхождения.

    ⚠️ Не вырезаем целиком: «да, да, да» бывает настоящим, и по тексту их не различить.
    Один лишний токен дешевле потерянной речи.
    """
    from make_record import _collapse
    assert _collapse("Так. Извание Извание Извание Извание Извание дальше") == "Так. Извание дальше"


def test_ordinary_repetition_survives():
    """Троекратный повтор — живая речь, а не галлюцинация: порог начинается с четырёх."""
    from make_record import _collapse
    text = "ну да, да, да, понятно"
    assert _collapse(text) == text


def test_words_lose_exactly_the_dropped_tokens():
    """Слова чистятся по тем же границам, что и текст: иначе подсветка покажет несуществующее."""
    from make_record import drop_degenerate
    ws = [["Извание", i, i + 0.5] for i in range(6)] + [["дальше", 9.0, 9.4]]
    x = {"markdown": "---\ntitle: x\n---\n\n[S] <!-- t:0.0 --> "
                     "Извание Извание Извание Извание Извание Извание дальше",
         "turns": [{"text": "Извание Извание Извание Извание Извание Извание дальше"}],
         "words": {"turns": [{"words": ws}]}}
    drop_degenerate(x)
    left = [w[0] for w in x["words"]["turns"][0]["words"]]
    assert left == ["Извание", "дальше"], left
    assert x["turns"][0]["text"] == "Извание дальше"


def test_clean_record_is_untouched():
    """На чистой записи функция не должна менять ни текста, ни времён."""
    from make_record import drop_degenerate
    ws = [["раз", 0, 1], ["два", 1, 2], ["три", 2, 3]]
    x = {"markdown": "---\ntitle: x\n---\n\n[S] <!-- t:0.0 --> раз два три",
         "turns": [{"text": "раз два три"}], "words": {"turns": [{"words": list(ws)}]}}
    assert drop_degenerate(x) == 0
    assert x["words"]["turns"][0]["words"] == ws


# --- цензура ---------------------------------------------------------------
#
# Корень вместо написания — единственное, чем цензура отличается от гарбла. Проверяем на
# синтетическом корне: тест не должен зависеть от того, что сегодня лежит в корпусе.

def censor_doc() -> dict:
    """Мини-артефакт: те же три места, куда обязана дойти замена."""
    return {
        "markdown": "[Пётр] <!-- t:1.0 --> Кряк, кряка и крякву не трогаем.\n",
        "turns": [{"speaker": "Пётр", "text": "Кряк, кряка и крякву не трогаем."}],
        "words": {"turns": [{"words": [["Кряк,", 1.0, 1.2], ["кряка", 1.3, 1.5],
                                       ["и", 1.6, 1.7], ["крякву", 1.8, 2.0]]}]},
    }


def test_censor_catches_the_whole_family_of_forms(tmp_path):
    """Корень ловит любую форму и любой регистр — в отличие от гарбла, где форма своя строка."""
    x = censor_doc()
    make_record.apply_censor(x, [{"root": "кряк", "now": "кхм"}])
    assert "Кряк" not in x["markdown"] and "кряка" not in x["markdown"]


def test_censor_spares_what_except_lists(tmp_path):
    """Корень цепляет и законные слова с той же основой — на это есть `except`."""
    x = censor_doc()
    make_record.apply_censor(x, [{"root": "кряк", "now": "кхм", "except": ["крякву"]}])
    assert "крякву" in x["markdown"], "законное слово из `except` трогать нельзя"
    assert "кряка" not in x["markdown"], "остальные формы обязаны замениться"


def test_censor_reaches_body_turns_and_words(tmp_path):
    """Все три места разом: иначе текст и караоке разъедутся навсегда."""
    x = censor_doc()
    make_record.apply_censor(x, [{"root": "кряк", "now": "кхм"}])
    assert "Кряк" not in x["markdown"]
    assert "Кряк" not in x["turns"][0]["text"]
    assert all("кряк" not in w[0].lower() for w in x["words"]["turns"][0]["words"])


def test_censor_keeps_word_timings(tmp_path):
    """Замена один-в-один: число слов и их времена не двигаются, перевыравнивание не нужно."""
    x = censor_doc()
    before = [(w[1], w[2]) for w in x["words"]["turns"][0]["words"]]
    make_record.apply_censor(x, [{"root": "кряк", "now": "кхм"}])
    after = x["words"]["turns"][0]["words"]
    assert [(w[1], w[2]) for w in after] == before
    assert len(after) == len(before)


def test_censor_refuses_a_replacement_with_a_space(tmp_path):
    """⚠️ Пробел в замене делает «слово» с пробелом в пословных временах, после чего разрез на
    абзацы молча отключается — часовой доклад становится одной репликой. Лучше отказ."""
    x = censor_doc()
    with pytest.raises(SystemExit):
        make_record.apply_censor(x, [{"root": "кряк", "now": "два слова"}])


def test_censor_counts_replacements_in_the_body_only(tmp_path):
    """Счётчик считает тело. По всем трём местам он показал бы утроенное число."""
    x = censor_doc()
    applied = make_record.apply_censor(x, [{"root": "кряк", "now": "кхм"}])
    assert applied[0]["count"] == 3


def test_censor_is_loaded_separately_from_garbles(tmp_path):
    """`load_dicts` отдаёт цензуру третьим значением, а не подмешивает её к гарблам."""
    root = corpus(tmp_path, fixes={
        "global": [{"was": "Кафку", "now": "Kafka"}], "records": {},
        "censor": {"rules": [{"root": "кряк", "now": "кхм"}]}})
    names, fixes, censor = make_record.load_dicts(root, "любая-запись")
    assert [f["was"] for f in fixes] == ["Кафку"], "цензура не должна попасть в гарблы"
    assert [c["root"] for c in censor] == ["кряк"]


# --- разрез на абзацы: конец предложения важнее паузы ------------------------

def words_at(spec: list[tuple[str, float, float]]) -> list:
    return [[w, a, b] for w, a, b in spec]


def test_абзац_режется_по_концу_предложения_а_не_по_паузе():
    """⚠️ Отменяет прежнее правило «самая длинная пауза и есть конец мысли».

    Замерено на корпусе: по паузе 21% разрезов приходились на середину фразы — 3705 обрывов
    на полуслове, и в читалке это видно сразу. После предпочтения точки осталось 4%.

    Здесь пауза ПОСРЕДИ фразы длиннее, чем на конце предложения. Победить должна точка.
    """
    spec = [("Первое", 0.0, 1.0), ("предложение.", 1.1, 2.0),      # точка, пауза после — 0.5
            ("Второе", 2.5, 3.0), ("тянется", 5.0, 6.0),           # запинка в 2.0 — длиннее
            ("дальше", 6.1, 7.0)]
    out = make_record.paragraphs_of(words_at(spec), target=1.5)
    assert [w[0] for w in out[0]] == ["Первое", "предложение."], \
        "разрез обязан лечь на точку, а не на самую длинную паузу"


def test_без_точки_в_окне_режем_по_паузе_как_раньше():
    """Запасное правило никуда не делось: речь без единой точки резать всё равно надо."""
    spec = [("тянется", 0.0, 1.0), ("без", 1.1, 1.5), ("точки", 1.6, 2.0),
            ("совсем", 4.0, 5.0), ("долго", 5.1, 6.0)]
    out = make_record.paragraphs_of(words_at(spec), target=1.5)
    assert len(out) > 1 and [w[0] for w in out[0]][-1] == "точки", \
        "без точки разрез должен лечь на самую длинную паузу"


def test_сокращение_за_конец_предложения_не_считается():
    """⚠️ «т.е.» и «и т.д.» тоже носят точку. Разрез по ним рвал бы фразу так же, как запинка,
    поэтому требуем, чтобы следующее слово начиналось с заглавной или цифры."""
    assert make_record.sentence_end("предложение.", "Дальше") is True
    assert make_record.sentence_end("предложение.", "12") is True
    assert make_record.sentence_end("т.е.", "дальше") is False
    assert make_record.sentence_end("слово", "Дальше") is False
    assert make_record.sentence_end("«цитата.»", "Дальше") is True


# --- роли: кто выступал, кто участвовал ------------------------------------------------
#
# Имена синтетические. Тело — три названных голоса и один безымянный; эфир задаёт `words`.

ROLE_BODY = "\n".join([
    "[Мария Кузнецова] <!-- t:0.0 --> Всем привет, начинаем.",
    "[Пётр Ковалёв] <!-- t:10.0 --> Сегодня расскажу про Kafka.",
    "[Speaker_7] <!-- t:900.0 --> А вопрос можно?",
    "[Олег Соколов] <!-- t:910.0 --> Да, а почему не Redis?",
])
ROLE_WORDS = {"turns": [
    {"speaker": "Мария Кузнецова", "speaker_id": "Speaker_1", "start": 0.0, "end": 9.0},
    {"speaker": "Пётр Ковалёв", "speaker_id": "Speaker_2", "start": 10.0, "end": 900.0},
    {"speaker": "Speaker_7", "speaker_id": "Speaker_7", "start": 900.0, "end": 905.0},
    {"speaker": "Олег Соколов", "speaker_id": "Speaker_3", "start": 910.0, "end": 930.0},
]}
TALK = {"model": "talk"}


def test_roles_talk_from_calendar():
    """Докладчик по календарю (форма имени другая — «Петр»), остальные названные — участники."""
    meta = {"speakers": [{"name": "Петр Ковалёв", "from": "calendar"}]}
    r = make_record.roles_of(ROLE_BODY, ROLE_WORDS, meta, TALK)
    assert r == {"speakers": ["Пётр Ковалёв"], "participants": ["Мария Кузнецова", "Олег Соколов"],
                 "voices": 4}


def test_roles_calendar_name_without_voice_is_kept():
    """Докладчик назван в календаре, но в звуке безымянен: имя источника идёт в шапку как есть."""
    meta = {"speakers": [{"name": "Зоя Воробьёва", "from": "calendar"}]}
    r = make_record.roles_of(ROLE_BODY, ROLE_WORDS, meta, TALK)
    assert r["speakers"] == ["Зоя Воробьёва"]
    assert "Пётр Ковалёв" in r["participants"]


def test_roles_talk_falls_back_to_dominant_voice_only():
    """Без календаря и меты докладчик — самый долгий голос, и только если он говорит заметную
    долю: тихий ведущий, открывший встречу с безымянным докладчиком, докладчиком не станет."""
    r = make_record.roles_of(ROLE_BODY, ROLE_WORDS, {}, TALK)
    assert r["speakers"] == ["Пётр Ковалёв"]
    quiet = {"turns": [{"speaker": "Мария Кузнецова", "speaker_id": "Speaker_1", "start": 0, "end": 30},
                       {"speaker": "Speaker_9", "speaker_id": "Speaker_9", "start": 30, "end": 900}]}
    body = "[Мария Кузнецова] <!-- t:0.0 --> Начинаем.\n[Speaker_9] <!-- t:30.0 --> Расскажу."
    r = make_record.roles_of(body, quiet, {}, TALK)
    assert r["speakers"] == [] and r["participants"] == ["Мария Кузнецова"] and r["voices"] == 2


def test_roles_meeting_and_course():
    r = make_record.roles_of(ROLE_BODY, ROLE_WORDS, {}, {"model": "meeting"})
    assert r["speakers"] == [] and len(r["participants"]) == 3
    r = make_record.roles_of(ROLE_BODY, ROLE_WORDS, {}, {"model": "course"})
    assert r["speakers"] == ["Пётр Ковалёв"]


def test_roles_owner_override_wins():
    """Рука владельца: имя уходит в свой список и убирается из другого."""
    meta = {"speakers": [{"name": "Пётр Ковалёв", "from": "calendar"}],
            "roles": {"speakers": ["Олег Соколов"], "participants": ["Пётр Ковалёв"]}}
    r = make_record.roles_of(ROLE_BODY, ROLE_WORDS, meta, TALK)
    assert r["speakers"] == ["Олег Соколов"]
    assert r["participants"] == ["Мария Кузнецова", "Пётр Ковалёв"]


def test_same_person_tolerates_spellings():
    assert make_record.same_person("Люба Кузнецова", "Любовь Кузнецова")   # уменьшительное
    assert make_record.same_person("Ковалёв", "Пётр Ковалёв")               # фамилия без имени
    assert make_record.same_person("Лев Семёнов", "Лев Семенов")             # ё/е
    assert not make_record.same_person("Марк Соколов", "Макар Соколов")     # разные имена


# --- категория, темы, год ------------------------------------------------------------------

def test_labels_owner_over_classifier():
    """`labels` (рука владельца) побеждает `classification`; пусто — пусто, а не догадка."""
    meta = {"classification": {"category": "Очереди", "topics": ["Kafka", "Redis"], "kind": "Воркшоп"},
            "labels": {"category": "Базы данных", "topics": ["PostgreSQL"]}}
    assert make_record.labels_of(meta) == {"category": "Базы данных", "topics": ["PostgreSQL"], "kind": "Воркшоп"}
    assert make_record.labels_of({}) == {"category": "", "topics": [], "kind": ""}
    only_auto = make_record.labels_of({"classification": {"category": "Очереди", "topics": ["Kafka"]}})
    assert only_auto["category"] == "Очереди" and only_auto["topics"] == ["Kafka"]


def test_year_from_course_name_not_upload_date(tmp_path):
    """У лекций дата — дата ВЫКЛАДКИ; год курса стоит во втором уровне каталога."""
    course = tmp_path / "records" / "Курс" / "Питон 2023" / "2024-08-01-zanyatie-1"
    talk = tmp_path / "records" / "Летучка" / "2024" / "2024-08-01-doklad"
    assert make_record.year_of("2024-08-01", course) == "2023"
    assert make_record.year_of("2024-08-01", talk) == "2024"
    assert make_record.year_of("2026-03-12", tmp_path / "records" / "flat") == "2026"


def test_header_carries_category_topics_year(tmp_path):
    root = corpus(tmp_path)
    (root / "inbox" / "Планёрка 12 марта.meta.json").write_text(json.dumps({
        "classification": {"category": "Очереди", "topics": ["Kafka"], "kind": "Доклад"},
    }, ensure_ascii=False), encoding="utf-8")
    head = head_of(build(root))
    assert head["category"] == "Очереди" and head["topics"] == ["Kafka"]
    assert head["year"] == "2026" and head["kind"] == ["Доклад"]
