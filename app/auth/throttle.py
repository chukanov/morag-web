"""Лимит попыток входа — по логину и по адресу.

Главная цель не перебор, а БЛОКИРОВКА УЧЁТКИ: повторные неверные bind'ы блокируют доменную
учётку целиком (почта, всё), порог в политике домена обычно 5-10 за полчаса. Поэтому лимит на
логин держим ниже порога: сайт не должен быть тем местом, где коллега заблокировал себе почту,
трижды промахнувшись по клавише. Лимит на адрес — второй, от перебора логинов подряд.

Не `RateLimiter` из `app/limits.py`: тот — ведро с арендой слота под долгие потоки, а здесь
нужен счётчик неудач в окне, который успех обнуляет.
"""

from __future__ import annotations

import time
from collections import deque


class LoginThrottle:
    def __init__(self, attempts: int, window_s: float, per_ip_attempts: int):
        self.attempts = attempts
        self.window = window_s
        self.per_ip = per_ip_attempts
        self._fails: dict[str, deque] = {}

    def _prune(self, key: str, now: float) -> deque:
        q = self._fails.get(key)
        if q is None:
            return deque()
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            self._fails.pop(key, None)
        return q

    def retry_after(self, login: str | None, ip: str | None, now: float | None = None) -> int:
        """Сколько секунд ждать (0 — можно пробовать). Считается ДО обращения к каталогу."""
        now = now if now is not None else time.time()
        wait = 0
        for key, cap in ((f"l:{login}", self.attempts), (f"ip:{ip}", self.per_ip)):
            if cap <= 0 or key.endswith(":None"):
                continue
            q = self._prune(key, now)
            if len(q) >= cap:
                wait = max(wait, int(self.window - (now - q[0])) + 1)
        return wait

    def failed(self, login: str | None, ip: str | None, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for key in (f"l:{login}", f"ip:{ip}"):
            if key.endswith(":None"):
                continue
            self._prune(key, now)
            self._fails.setdefault(key, deque()).append(now)

    def cleared(self, login: str | None, ip: str | None) -> None:
        """Успешный вход обнуляет счётчик логина: дальше промахи считаются заново. Адрес не
        трогаем — с него могли перебирать другие логины."""
        self._fails.pop(f"l:{login}", None)
