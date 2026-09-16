"""make_blurb.py: сводка нужна только записям без авторской аннотации и без готовой сводки; вход —
начало речи без шапки, тайм-кодов и меток; ответ модели чистится от обёрток."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import make_blurb as mb  # noqa: E402

MD = '---\ntitle: "Kafka без боли"\ndate: "2026-01-01"\nbranch: "Митапы"\ncategory: "Инфраструктура"\n{summary}---\n\n[Speaker_1] <!-- t:2.3 --> Всем привет! Сегодня про Kafka.\n\n[Speaker_1] <!-- t:40.0 --> Начнём с того, зачем она нам.\n'


def _rec(root: Path, name: str, summary: str = "", blurb: str | None = None) -> Path:
    d = root / "Митапы" / "2026" / name
    d.mkdir(parents=True)
    (d / "record.md").write_text(MD.format(summary=f'summary: "{summary}"\n' if summary else ""), encoding="utf-8")
    if blurb is not None:
        (d / "record.meta.json").write_text(json.dumps({"blurb": {"text": blurb}}), encoding="utf-8")
    return d


def test_candidates_skip_annotated_and_done(tmp_path: Path):
    a = _rec(tmp_path, "2026-01-01-a")                       # нужна
    _rec(tmp_path, "2026-01-02-b", summary="Авторская аннотация")   # есть аннотация поста
    _rec(tmp_path, "2026-01-03-c", blurb="Уже есть")          # сводка уже есть
    d = _rec(tmp_path, "2026-01-04-d", blurb="")              # пустая сводка — нужна
    assert [r.name for r in mb.candidates(tmp_path)] == [a.name, d.name]
    assert len(mb.candidates(tmp_path, redo=True)) == 3        # --redo: все без аннотации


def test_speech_head_strips_header_timecodes_and_labels():
    head = mb.speech_head(MD.format(summary=""), words=6)
    assert head == "Всем привет! Сегодня про Kafka. Начнём"
    assert "t:" not in head and "Speaker" not in head and "title" not in head


def test_prompt_and_clean(tmp_path: Path):
    rec = _rec(tmp_path, "2026-01-01-a")
    p = mb.build_prompt(rec)
    assert "Заголовок: Kafka без боли" in p and "Раздел: Митапы · Инфраструктура" in p
    assert "Всем привет! Сегодня про Kafka." in p
    assert mb.clean('  «Про Kafka.»\n\n') == "Про Kafka."
    # обёртки вопреки промпту — снимаются, первая буква снова заглавная
    assert mb.clean("О чём запись: релизы в отделе.") == "Релизы в отделе."
    assert mb.clean("Краткое содержание записи — обзор докладов.") == "Обзор докладов."
    assert mb.clean("Содержание записи: про Kafka.") == "Про Kafka."
    assert mb.prompt_id("x").startswith("Instruct:")
