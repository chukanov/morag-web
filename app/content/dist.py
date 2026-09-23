"""Зеркало установщика: что лежит в каталоге раздачи и по какому пропуску его оттуда берут.

Зачем зеркало. Приложение «Загрузить запись» ставится на чужой Mac без прав администратора и без
учёток на стороне: портативный питон, статический ffmpeg, снимок инструментов и модели (в том
числе pyannote — единственная, что требовала токена Hugging Face и нажатия «Agree») скачиваются
С САЙТА. Сервер тут — обычная файловая раздача, всё содержимое готовит владелец корпуса
(`build_dist.py` в приватном репозитории) и везёт rsync'ом.

⚠️ **Пропуск, а не сессия.** Ставится всё одной строкой в терминале (`curl … | sh`), а у `curl`
cookie сайта нет: класть туда настоящую сессию значило бы носить удостоверение сотрудника в
командной строке и в истории shell. Поэтому страница выдаёт вошедшему отдельный подписанный
пропуск: он живёт неделю (скачивание трёх гигабайт по корпоративной сети — это часы, а
установку откладывают на вечер), ничего о человеке не говорит и даёт ровно одно право — брать
файлы этого каталога. Подпись — ключ, выведенный из ключа сессий (`AuthService.derived_secret`):
корень один, но пропуском нельзя притвориться сессией и наоборот.

⚠️ Имя файла проверяется белым списком и берётся ТОЛЬКО с верхнего уровня каталога: раздача,
где имя приезжает из адреса, — классическое место для `../../etc/passwd`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

TTL = 7 * 24 * 3600          # неделя: скачивание долгое, а установку откладывают
PURPOSE = "ingest-dist"      # назначение подписи (см. `derived_secret`)
MANIFEST = "manifest.json"
# Имя файла зеркала: буквы, цифры и `.-_`, без путей и без ведущей точки.
ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _mac(secret: bytes, body: str) -> str:
    """Половина SHA-256 (128 бит) — подделать нельзя, а строка короче: её человек копирует
    целиком вместе с командой, и лишние сорок знаков читаются как страшный шум."""
    return _b64(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()[:16])


def issue(secret: bytes, *, ttl: int = TTL, now: float | None = None) -> str:
    """Пропуск `<срок>.<подпись>`; срок — минуты эпохи в 36-ричной записи (шесть знаков)."""
    exp = int(((now if now is not None else time.time()) + ttl) // 60)
    body = _base36(exp)
    return f"{body}.{_mac(secret, body)}"


def valid(token: str, secret: bytes, *, now: float | None = None) -> bool:
    body, _, mac = (token or "").partition(".")
    if not body or not mac or not hmac.compare_digest(_mac(secret, body), mac):
        return False
    try:
        exp = int(body, 36)
    except ValueError:
        return False
    return exp * 60 > (now if now is not None else time.time())


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while value:
        value, rest = divmod(value, 36)
        out = digits[rest] + out
    return out or "0"


def safe_name(name: str) -> bool:
    return bool(name) and len(name) <= 80 and name[0] != "." and set(name) <= ALLOWED


def file_of(root: Path, name: str) -> Path | None:
    """Файл зеркала по имени — или None. Только верхний уровень каталога и только обычный файл."""
    if not safe_name(name):
        return None
    path = root / name
    return path if path.is_file() and path.parent == root else None


def catalog(root: Path) -> dict:
    """Что лежит на зеркале: манифест владельца плюс РАЗМЕР С ДИСКА.

    Размер берётся у файла, а не из манифеста: манифест пишет сборщик на своей машине, а
    приезжает содержимое rsync'ом, и оборванная доставка выглядела бы на странице как готовое
    зеркало. Файл из манифеста, которого нет на диске, помечается `missing` — установщик на нём
    остановится с внятным словом, а не скачает 404 в tar.
    """
    if not root.is_dir():
        return {}
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = []
    total = 0
    for item in data.get("files") or []:
        name = str(item.get("name") or "")
        path = file_of(root, name)
        row = {**item, "name": name, "missing": path is None}
        if path:
            row["bytes"] = path.stat().st_size
            total += row["bytes"]
        files.append(row)
    return {**data, "files": files, "bytes": total}
