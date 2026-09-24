"""Инструменты экрана: доверие связке ключей машины.

⚠️ Сайт и LLM-шлюз подписаны ВНУТРЕННИМ центром сертификации, а httpx верит только `certifi`.
Без `truststore` разбор экрана падает на КАЖДОМ кадре (`CERTIFICATE_VERIFY_FAILED`) — ловилось
живьём 24.09. Инъекция в `upload.py` не помогает: подпроцессы экрана — другие процессы, и
`SSL_CERT_FILE` им экспортирует только враппер установщика, которого при запуске из чекаута нет.
"""
from __future__ import annotations

from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def test_screen_tools_trust_the_machine_keychain():
    src = (TOOLS / "describe_slides.py").read_text(encoding="utf-8")
    assert "truststore.inject_into_ssl()" in src, "инъекция доверия обязана быть"
    # …и именно в разборе окружения: эту функцию зовут оба инструмента до первого запроса.
    body = src[src.index("def load_env"):src.index("def relevant_terms") if "def relevant_terms" in src else len(src)]
    assert "truststore.inject_into_ssl()" in body, "инъекция живёт в load_env()"


def test_screen_refs_shares_that_env_loader():
    """Вторая половина чинится тем же кодом — значит, она обязана его и брать."""
    src = (TOOLS / "screen_refs.py").read_text(encoding="utf-8")
    assert "from describe_slides import" in src and "load_env" in src
