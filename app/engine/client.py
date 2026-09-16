"""HTTP-клиент к morag pipelines.

`trust_env=False` намеренно: движок локальный, и ambient HTTPS_PROXY не должен
ни заворачивать localhost, ни подмешивать корп-прокси в наши запросы.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx

log = logging.getLogger(__name__)


class EngineClient:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url.rstrip("/"),
            timeout=httpx.Timeout(
                connect=cfg.timeout.connect,
                read=cfg.timeout.read,  # пауза МЕЖДУ кадрами, не общий лимит
                write=cfg.timeout.write,
                pool=cfg.timeout.pool,
            ),
            trust_env=False,
            headers={"Authorization": f"Bearer {cfg.api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def stream_chat(self, messages: list[dict]):
        payload = {"model": self.cfg.model, "stream": True, "messages": messages}
        async with self._client.stream(
            "POST", "/v1/chat/completions", json=payload
        ) as response:
            yield response

    async def ping(self) -> bool:
        try:
            r = await self._client.get("/v1/models", timeout=3)
            return r.status_code == 200
        except httpx.HTTPError:
            return False
