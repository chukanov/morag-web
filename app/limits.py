"""Ограничители: потолок одновременных агент-циклов и rate-limit по адресу.

Ключ движка общий, каждый вопрос стоит денег и держит CPU — поэтому лишних
пускаем не в очередь (посетитель бы просто смотрел в пустоту), а сразу отказом.

Всё настраивается в `limits` конфига и снимается целиком (`rate_limit.enabled: false`)
или по одному рубежу (ноль в соответствующем поле). Защищаем ТОЛЬКО `/api/ask`:
статика, список записей и читалка бесплатны, и лимит там только вредил бы.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class Busy(Exception):
    """Свободных слотов нет прямо сейчас."""


class RateLimited(Exception):
    """Гость исчерпал свою квоту либо сайт упёрся в дневной потолок."""

    def __init__(self, retry_after: float, message: str) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.message = message


@dataclass
class Lease:
    """Расписка на один вопрос: что именно было списано и что возвращать."""

    key: str
    active: bool
    token: bool = False
    daily: bool = False


class ConcurrencyGate:
    def __init__(self, limit: int) -> None:
        self._sem = asyncio.Semaphore(max(1, limit))
        self.limit = max(1, limit)

    @property
    def free(self) -> int:
        return self._sem._value  # noqa: SLF001 — только для /api/health

    @asynccontextmanager
    async def slot(self):
        if self._sem.locked() and self._sem._value <= 0:  # noqa: SLF001
            raise Busy()
        acquired = False
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=0.05)
            acquired = True
        except (asyncio.TimeoutError, TimeoutError):
            raise Busy() from None
        try:
            yield
        finally:
            if acquired:
                self._sem.release()


def client_key(ip: str | None, ipv6_prefix: int = 64) -> str:
    """Ключ ведра.

    ⚠️ Для IPv6 ключом служит СЕТЬ /64, а не адрес: провайдер выдаёт клиенту целую
    подсеть, и лимит на конкретный адрес обходится бесплатно — просто взяв соседний.
    """
    if not ip:
        return "?"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/{ipv6_prefix}", strict=False))
    return str(addr)


class RateLimiter:
    """Три рубежа: ведро на адрес, один активный вопрос с адреса, дневной потолок.

    Ведро (token bucket), а не «N вопросов в час»: живой человек задаёт три вопроса
    подряд и уходит — всплеск переживаем, а равномерный долбёж скриптом душим.

    Дневной счётчик переживает перезапуск (лежит файлом рядом с журналом) — иначе
    рестарт обнулял бы именно ту защиту, которая стережёт кошелёк. Вёдра живут в
    памяти: для «подожди минуту» переживать рестарт незачем.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        burst: int = 5,
        refill_seconds: float = 120.0,
        per_ip_concurrent: int = 1,
        daily_total: int = 500,
        state_path: Path | str | None = None,
        ipv6_prefix: int = 64,
    ) -> None:
        self.enabled = enabled
        self.burst = max(0, burst)
        self.refill_seconds = max(1.0, float(refill_seconds))
        self.per_ip_concurrent = max(0, per_ip_concurrent)
        self.daily_total = max(0, daily_total)
        self.ipv6_prefix = ipv6_prefix
        self._state_path = Path(state_path) if state_path else None
        self._buckets: dict[str, tuple[float, float]] = {}   # ключ → (жетоны, когда считали)
        self._active: dict[str, int] = {}                     # ключ → активных вопросов
        self._day = ""
        self._used = 0
        self._lock = asyncio.Lock()
        self._load()

    # --- дневной счётчик -------------------------------------------------

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _load(self) -> None:
        self._day = self._today()
        if not self._state_path or not self._state_path.exists():
            return
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
            if saved.get("day") == self._day:
                self._used = int(saved.get("used", 0))
        except Exception:  # счётчик не должен мешать старту
            log.warning("не смог прочитать счётчик лимитов %s", self._state_path)

    def _save(self) -> None:
        if not self._state_path:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"day": self._day, "used": self._used}), encoding="utf-8"
            )
            tmp.replace(self._state_path)  # атомарно: иначе рестарт посреди записи бьёт файл
        except Exception:
            log.warning("не смог записать счётчик лимитов")

    @staticmethod
    def _seconds_to_midnight() -> int:
        now = time.gmtime()
        return max(1, 86400 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec))

    @property
    def used_today(self) -> int:
        return self._used if self._day == self._today() else 0

    # --- вёдра -----------------------------------------------------------

    def _tokens(self, key: str, now: float) -> float:
        tokens, seen = self._buckets.get(key, (float(self.burst), now))
        return min(float(self.burst), tokens + (now - seen) / self.refill_seconds)

    def _sweep(self, now: float) -> None:
        """Полное и давно не тронутое ведро неотличимо от отсутствующего — забываем.

        Без этого словарь растёт с каждым новым адресом: на публичном сайте это
        и утечка памяти, и способ навредить.
        """
        full_after = self.refill_seconds * max(1, self.burst)
        stale = [
            key for key, (_, seen) in self._buckets.items()
            if now - seen > full_after and not self._active.get(key)
        ]
        for key in stale:
            self._buckets.pop(key, None)

    # --- собственно проверка ---------------------------------------------

    async def acquire(self, ip: str | None) -> Lease:
        """Пропустить вопрос или отказать (`RateLimited`).

        Возвращает расписку: её надо `release()` по окончании потока и `refund()`,
        если вопрос так и не начался.
        """
        if not self.enabled:
            return Lease(key="", active=False)

        key = client_key(ip, self.ipv6_prefix)
        now = time.monotonic()
        async with self._lock:
            if len(self._buckets) > 4096:
                self._sweep(now)

            if self.per_ip_concurrent and self._active.get(key, 0) >= self.per_ip_concurrent:
                raise RateLimited(30, "Дождитесь ответа на предыдущий вопрос.")

            if self.daily_total:
                if self._day != self._today():          # наступили новые сутки
                    self._day, self._used = self._today(), 0
                if self._used >= self.daily_total:
                    raise RateLimited(
                        self._seconds_to_midnight(),
                        "На сегодня вопросы к базе закончились. Приходите завтра.",
                    )

            took_token = False
            if self.burst:
                tokens = self._tokens(key, now)
                if tokens < 1:
                    wait = (1 - tokens) * self.refill_seconds
                    raise RateLimited(
                        wait, f"Слишком часто. Следующий вопрос — через {int(wait) // 60 + 1} мин."
                    )
                self._buckets[key] = (tokens - 1, now)
                took_token = True

            took_daily = bool(self.daily_total)
            if took_daily:
                self._used += 1
                self._save()
            self._active[key] = self._active.get(key, 0) + 1

        return Lease(key=key, active=True, token=took_token, daily=took_daily)

    async def refund(self, lease: Lease) -> None:
        """Вопрос не начался (движок был занят) — возвращаем жетон и место в дневной квоте.

        Иначе отказ «занято» съедал бы квоту посетителя ни за что.
        """
        if not lease.active:
            return
        async with self._lock:
            if lease.token:
                tokens, _ = self._buckets.get(lease.key, (0.0, time.monotonic()))
                self._buckets[lease.key] = (min(float(self.burst), tokens + 1), time.monotonic())
                lease.token = False
            if lease.daily and self._used > 0:
                self._used -= 1
                self._save()
                lease.daily = False

    async def release(self, lease: Lease) -> None:
        if not lease.active:
            return
        async with self._lock:
            left = self._active.get(lease.key, 1) - 1
            if left > 0:
                self._active[lease.key] = left
            else:
                self._active.pop(lease.key, None)
        lease.active = False
