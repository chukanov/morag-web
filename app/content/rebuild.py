"""Очередь пересборки записей после правки имени.

Зачем очередь, а не пересборка прямо в запросе. Замерено на живом корпусе: одна запись
пересобирается 0.13–0.5 с, а у самого частого голоса 47 записей — это 15-20 секунд, на которые
браузер повис бы. Хуже другое: в записи звучит около семи голосов, и за один присест именования
одна и та же запись попала бы в пересборку много раз подряд. Очередь с дедупликацией по id
решает обе беды.

⚠️ Пересборка запускается КОМАНДОЙ из конфига, а не импортом инструмента. `app/` переезжает в
отдельный репозиторий веб-морды, инструменты корпуса остаются в этом — прямая зависимость
связала бы их навсегда. Цена: старт интерпретатора на запись (~0.15 с), то есть около семи
секунд на сорок семь записей. Универсальность этого стоит.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class Rebuilder:
    """Один воркер, одна очередь. Параллелить нечего: узкое место — диск и один и тот же файл."""

    def __init__(self, command: list[str], root: Path) -> None:
        self.command = list(command)
        self.root = Path(root)
        self._queue: asyncio.Queue[tuple[str, Path]] = asyncio.Queue()
        # ⚠️ Множество ждущих — не украшение: без него запись, у которой поправили три голоса,
        # пересобиралась бы трижды подряд с одним и тем же результатом.
        self._waiting: set[str] = set()
        self._task: asyncio.Task | None = None
        self._current: str = ""
        self.done: int = 0
        self.failed: list[str] = []

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def enqueue(self, records: dict[str, Path]) -> list[str]:
        """Поставить записи в очередь. Возвращает те, что действительно добавлены."""
        added = []
        for rid, directory in records.items():
            if rid in self._waiting or rid == self._current:
                continue
            self._waiting.add(rid)
            self._queue.put_nowait((rid, directory))
            added.append(rid)
        return added

    def status(self) -> dict:
        return {
            "pending": len(self._waiting),
            "current": self._current,
            "done": self.done,
            "failed": self.failed[-10:],
        }

    async def _run(self) -> None:
        while True:
            rid, directory = await self._queue.get()
            self._waiting.discard(rid)
            self._current = rid
            started = time.monotonic()
            try:
                argv = [a.format(record_dir=str(directory)) for a in self.command]
                proc = await asyncio.create_subprocess_exec(
                    *argv, cwd=str(self.root),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                out, _ = await proc.communicate()
                if proc.returncode:
                    self.failed.append(rid)
                    log.error("пересборка %s не удалась: %s", rid,
                              out.decode("utf-8", "replace")[-400:])
                else:
                    self.done += 1
                    log.info("пересобрано %s за %.2f с", rid, time.monotonic() - started)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failed.append(rid)
                log.exception("пересборка %s сорвалась", rid)
            finally:
                self._current = ""
                self._queue.task_done()
