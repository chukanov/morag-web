"""Приватный журнал владельца.

Материал для разбора провалов и регресс-набора («улучшения от наблюдаемых
провалов»). Посетителям недоступен, в git не попадает.
Оценки пишутся отдельной строкой: jsonl не переписываем, join по answer_id.

IP пишется как есть (решение владельца 2026-08-11). Раньше он хэшировался с солью;
отказались, потому что защищаться было не от чего: чужих диалогов посетителям и так
не видно (боль OWUI была именно в этом), а адрес всё равно оседает в логах хостера.
Взамен адрес стало видно в журнале и лимитах — то есть видно, кто именно долбит.
Старые записи с полем `ip_hash` остались как есть, историю не переписывали.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


class Journal:
    def __init__(self, path: Path, *, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self._lock = asyncio.Lock()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    async def write(self, record: dict) -> None:
        if not self.enabled:
            return
        record.setdefault("ts", time.time())
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            async with self._lock:
                await asyncio.to_thread(self._append, line)
        except Exception:  # журнал не должен ломать ответ посетителю
            log.exception("не смог записать в журнал")

    def _append(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
