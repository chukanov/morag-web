"""Журнал владельца: запись обязана пережить отмену задачи.

Журнал пишется в `finally` обработчика ответа, и туда попадают не только
удачные ответы, но и брошенные — посетитель закрыл вкладку, Starlette отменил
задачу. Это САМЫЕ ценные записи: по ним видно, где людям не хватило терпения.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.journal import Journal  # noqa: E402


async def _wait_file(path: Path, tries: int = 50) -> str:
    """Запись уходит в поток — даём ей долететь, но не залипаем навсегда."""
    for _ in range(tries):
        if path.is_file() and path.read_text(encoding="utf-8").strip():
            return path.read_text(encoding="utf-8")
        await asyncio.sleep(0.02)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


async def test_запись_переживает_отмену(tmp_path: Path):
    """Посетитель ушёл, не дождавшись, — вопрос всё равно обязан попасть в журнал.

    Ловилось на живом госте 2026-08-18: он ждал 70 с, закрыл вкладку, и вопрос
    исчез — узнали о нём только из лога движка.
    """
    path = tmp_path / "journal.jsonl"
    journal = Journal(path)
    started = asyncio.Event()

    async def answering():
        try:
            started.set()
            await asyncio.sleep(30)  # «идёт ответ»
        finally:
            await journal.write({"type": "answer", "status": "aborted", "question": "какой LLM Harness лучше?"})

    task = asyncio.create_task(answering())
    await started.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    text = await _wait_file(path)
    assert text.strip(), "брошенный вопрос потерян — самый ценный случай"
    rec = json.loads(text.strip())
    assert rec["status"] == "aborted" and rec["question"].startswith("какой LLM")
    assert rec.get("ts"), "без времени запись бесполезна"


async def test_обычная_запись_и_дозапись(tmp_path: Path):
    path = tmp_path / "journal.jsonl"
    journal = Journal(path)
    await journal.write({"type": "answer", "n": 1})
    await journal.write({"type": "vote", "vote": "up"})
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(l)["type"] for l in lines] == ["answer", "vote"]


async def test_выключенный_журнал_молчит(tmp_path: Path):
    path = tmp_path / "off.jsonl"
    await Journal(path, enabled=False).write({"type": "answer"})
    assert not path.exists()
