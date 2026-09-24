"""Авто-тема сессии.

После первого ответа делаем ОДИН дешёвый вызов LLM: «обобщи вопрос и ответ в
тему из 3-6 слов». Морагу правок не нужно — тема живёт целиком у нас.

Цена и риск: у BFF появляется зависимость от LLM-эндпоинта, которой раньше не
было. Поэтому вызов короткий, с жёстким таймаутом, и любая осечка просто
означает «темы не будет» — ответ посетителю от этого не страдает.
"""

from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger(__name__)

SYSTEM = (
    "Ты озаглавливаешь разговор человека с базой знаний по записям внутренних митапов. "
    "По вопросу (и списку докладов корпуса, если он дан) придумай заголовок разговора. "
    "Не отвечай на сам вопрос. Отвечай СТРОГО двумя строками:\n"
    "Тема: <3-6 слов, именная группа, без кавычек и точки>\n"
    "Пояснение: <одно предложение до 15 слов, о чём пойдёт речь>"
)
_TITLE_RE = re.compile(r"^\s*тема\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_SUMMARY_RE = re.compile(r"^\s*пояснени[ея]\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_MAX_CATALOG = 2000  # каталог даём кратко: тема — это заголовок, а не пересказ


class TopicMaker:
    def __init__(self, cfg, api_key: str = "") -> None:
        self.cfg = cfg
        self.key = api_key or cfg.api_key
        self.enabled = bool(cfg.enabled and self.key)
        self._client: httpx.AsyncClient | None = None
        if not self.enabled:
            log.info("авто-тема выключена (нет ключа LLM) — кадр topic отдаваться не будет")

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.cfg.base_url.rstrip("/"),
                timeout=self.cfg.timeout,
                trust_env=False,  # прокси только явный: чужой из env нам тут не нужен
                proxy=self.cfg.proxy,
                headers={"Authorization": f"Bearer {self.key}"},
            )
        return self._client

    async def reachable(self, timeout: float = 5.0) -> bool | None:
        """Отвечает ли вообще LLM-эндпоинт. `None` — спросить не у кого (ключа нет).

        ⚠️ Зачем это здесь, а не в мораге: когда движок молчит, человек видит «не получилось» и
        не может отличить «упал поиск» от «лёг LLM-шлюз». Инциденты 17–18.09 стоили часа именно
        на этом различении. Спрашиваем ДЕШЁВОЕ (`/models`) и только на пути ошибки — здоровому
        ответу этот запрос не достаётся никогда.

        ⓘ Это тот же эндпоинт, которым мы делаем тему. Если у движка он другой, ответ говорит
        «сеть до LLM живая», и это всё равно больше, чем ничего.
        """
        if not self.key:
            return None
        try:
            answer = await self._http().get("/models", timeout=timeout)
        except httpx.HTTPError as exc:
            log.warning("LLM-эндпоинт не отвечает: %s", type(exc).__name__)
            return False
        # 401/403 — ключ, а не сеть: эндпоинт ЖИВ, и валить на него ответ движка нечестно.
        return answer.status_code < 500

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def for_question(self, question: str, catalog: str = "") -> dict | None:
        """Тема по вопросу — БЕЗ ожидания ответа.

        Так вызов уходит параллельно с поиском агента (20-50 с) и тема успевает
        появиться, пока посетитель всё равно ждёт, а не после всего.
        """
        if not self.enabled or not question.strip():
            return None
        user = f"Вопрос слушателя: {question.strip()}"
        if catalog:
            user += f"\n\nО чём этот корпус (доклады): {catalog[:_MAX_CATALOG]}"
        payload = {
            "model": self.cfg.model,
            "temperature": 0.2,
            # Рассуждающие модели (тот же deepseek-v4-flash) тратят выходные токены
            # на размышления — замерено от 0 до 330 на одинаковых запросах. Просим
            # их отключить И даём запас: иначе лимит съедается и content приходит
            # пустым или обрезанным на полуслове.
            "reasoning": {"enabled": False},
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
        }
        try:
            # Замерено: обычный вызов идёт 2-3 с при таймауте 12. Редкая осечка
            # прокси — не повод остаться без темы, поэтому одна повторная попытка.
            try:
                response = await self._http().post("/chat/completions", json=payload)
            except (httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                log.info("тема: повтор после %s", type(exc).__name__)
                response = await self._http().post("/chat/completions", json=payload)
            if response.status_code != 200:
                log.warning("тема: LLM ответил %s", response.status_code)
                return None
            data = response.json()
            choice = data["choices"][0]
            text = (choice["message"].get("content") or "").strip()
            if not text:
                # чаще всего это «рассуждения съели лимит» — пишем цифры, чтобы
                # не гадать в следующий раз
                usage = data.get("usage") or {}
                log.warning(
                    "тема: пустой ответ (finish=%s, выход=%s, рассуждения=%s)",
                    choice.get("finish_reason"),
                    usage.get("completion_tokens"),
                    (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                )
                return None
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("тема: не получилось (%s)", type(exc).__name__)
            return None

        return _parse(text)


def _parse(text: str) -> dict | None:
    """Модель иногда игнорирует формат — тогда берём первую внятную строку."""
    title_match = _TITLE_RE.search(text)
    summary_match = _SUMMARY_RE.search(text)

    title = (title_match.group(1) if title_match else "").strip(" .·—-«»\"'")
    summary = (summary_match.group(1) if summary_match else "").strip()

    if not title:
        lines = [l.strip(" .·—-«»\"'") for l in text.splitlines() if l.strip()]
        if not lines:
            return None
        title = lines[0]
        if not summary and len(lines) > 1:
            summary = lines[1]

    title = " ".join(title.split())
    if len(title) > 70:  # заголовок должен быть заголовком
        title = title[:69].rstrip() + "…"
    return {"title": title, "summary": " ".join(summary.split())}
