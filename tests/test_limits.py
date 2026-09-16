"""Rate-limit: ведро на адрес, один вопрос с адреса, дневной потолок.

Время не ждём — двигаем часы (`time.monotonic`/`time.gmtime` подменяются), иначе
тест на восстановление жетонов шёл бы минутами.
"""

from __future__ import annotations

import json

import pytest

from app.limits import Busy, ConcurrencyGate, RateLimited, RateLimiter, client_key


@pytest.fixture
def clock(monkeypatch):
    """Управляемые часы: тесты двигают их сами."""
    now = {"t": 1000.0}
    monkeypatch.setattr("app.limits.time.monotonic", lambda: now["t"])
    return now


def make(tmp_path, **kw):
    kw.setdefault("state_path", tmp_path / "ratelimit.json")
    return RateLimiter(**kw)


async def test_ведро_даёт_запас_и_потом_отказывает(tmp_path, clock):
    rl = make(tmp_path, burst=3, refill_seconds=120, per_ip_concurrent=0, daily_total=0)
    for _ in range(3):
        await rl.release(await rl.acquire("1.2.3.4"))
    with pytest.raises(RateLimited) as err:
        await rl.acquire("1.2.3.4")
    assert err.value.retry_after > 0


async def test_жетоны_капают_со_временем(tmp_path, clock):
    rl = make(tmp_path, burst=2, refill_seconds=120, per_ip_concurrent=0, daily_total=0)
    for _ in range(2):
        await rl.release(await rl.acquire("1.2.3.4"))
    with pytest.raises(RateLimited):
        await rl.acquire("1.2.3.4")

    clock["t"] += 120  # накапал ровно один
    await rl.release(await rl.acquire("1.2.3.4"))
    with pytest.raises(RateLimited):
        await rl.acquire("1.2.3.4")


async def test_адреса_не_мешают_друг_другу(tmp_path, clock):
    rl = make(tmp_path, burst=1, refill_seconds=120, per_ip_concurrent=0, daily_total=0)
    await rl.release(await rl.acquire("1.1.1.1"))
    await rl.release(await rl.acquire("2.2.2.2"))  # соседу отказ первого не мешает


async def test_ipv6_считается_подсетью_а_не_адресом(tmp_path, clock):
    """Иначе лимит обходится бесплатно: у клиента целая /64."""
    rl = make(tmp_path, burst=1, refill_seconds=120, per_ip_concurrent=0, daily_total=0)
    await rl.release(await rl.acquire("2001:db8:1:2::1"))
    with pytest.raises(RateLimited):
        await rl.acquire("2001:db8:1:2::ffff")  # тот же /64
    await rl.release(await rl.acquire("2001:db8:1:3::1"))  # другая сеть — можно

    assert client_key("2001:db8:1:2::1") == client_key("2001:db8:1:2::9999")
    assert client_key("10.0.0.1") == "10.0.0.1"


async def test_один_вопрос_с_адреса_одновременно(tmp_path, clock):
    rl = make(tmp_path, burst=10, refill_seconds=1, per_ip_concurrent=1, daily_total=0)
    lease = await rl.acquire("1.2.3.4")
    with pytest.raises(RateLimited):
        await rl.acquire("1.2.3.4")
    await rl.release(lease)
    await rl.release(await rl.acquire("1.2.3.4"))  # отпустили — снова можно


async def test_дневной_потолок_общий_для_всех(tmp_path, clock):
    rl = make(tmp_path, burst=100, refill_seconds=1, per_ip_concurrent=0, daily_total=2)
    await rl.release(await rl.acquire("1.1.1.1"))
    await rl.release(await rl.acquire("2.2.2.2"))
    with pytest.raises(RateLimited) as err:
        await rl.acquire("3.3.3.3")  # потолок общий, адрес не спасает
    assert err.value.retry_after > 60  # ждать до полуночи, а не минуту
    assert rl.used_today == 2


async def test_дневной_счётчик_переживает_перезапуск(tmp_path, clock):
    path = tmp_path / "ratelimit.json"
    rl = make(tmp_path, burst=100, refill_seconds=1, per_ip_concurrent=0, daily_total=2)
    await rl.release(await rl.acquire("1.1.1.1"))
    assert json.loads(path.read_text())["used"] == 1

    again = make(tmp_path, burst=100, refill_seconds=1, per_ip_concurrent=0, daily_total=2)
    assert again.used_today == 1  # иначе рестарт обнулял бы защиту кошелька


async def test_отказ_движка_возвращает_квоту(tmp_path, clock):
    """«Занято» не должно съедать ни жетон, ни место в дневной квоте."""
    rl = make(tmp_path, burst=1, refill_seconds=120, per_ip_concurrent=1, daily_total=5)
    lease = await rl.acquire("1.2.3.4")
    await rl.refund(lease)
    await rl.release(lease)

    assert rl.used_today == 0
    await rl.release(await rl.acquire("1.2.3.4"))  # жетон на месте


async def test_выключенный_лимитер_пропускает_всё(tmp_path, clock):
    rl = make(tmp_path, enabled=False, burst=1, refill_seconds=999, daily_total=1)
    for _ in range(10):
        await rl.release(await rl.acquire("1.2.3.4"))
    assert rl.used_today == 0
    assert not (tmp_path / "ratelimit.json").exists()  # выключенный ничего не пишет


async def test_нулевые_поля_снимают_отдельные_рубежи(tmp_path, clock):
    rl = make(tmp_path, burst=0, refill_seconds=120, per_ip_concurrent=0, daily_total=0)
    for _ in range(50):
        await rl.release(await rl.acquire("1.2.3.4"))


async def test_вёдра_не_копятся_бесконечно(tmp_path, clock):
    """Словарь на публичном сайте — это утечка памяти и способ навредить."""
    rl = make(tmp_path, burst=1, refill_seconds=1, per_ip_concurrent=0, daily_total=0)
    for i in range(4200):
        await rl.release(await rl.acquire(f"10.0.{i // 256}.{i % 256}"))
    clock["t"] += 10  # все вёдра давно полны
    await rl.release(await rl.acquire("10.9.9.9"))  # любой вызов подметает
    assert len(rl._buckets) < 4200


async def test_гейт_отдаёт_busy_а_не_очередь(tmp_path):
    gate = ConcurrencyGate(1)
    async with gate.slot():
        with pytest.raises(Busy):
            async with gate.slot():
                pass
