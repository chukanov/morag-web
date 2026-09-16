"""Снимок учётки вошедшего на диске.

`data/auth/users/<провайдер>/<логин>.json` — имя, почта, должность, отдел, группы, DN, время
входа; рядом `<логин>.jpg`, если каталог отдал фото, и `<логин>.raw.json` — вся запись
каталога, только под флагом `auth.ldap.keep_raw_entry` (разовая отладка «что есть по учётке»).

Снимок — это и есть «сессия существует»: cookie несёт только логин, всё остальное читается
отсюда на каждый запрос. Удалил файл — человек разлогинен везде. Персональные данные коллег:
каталог вне git и вне доставки на сервер, обращение как с журналом.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# Логин — ASCII: буквы, цифры, точка, дефис, подчёркивание; первый знак — буква или цифра.
# Это ещё и имя файла снимка, поэтому ничего похожего на путь сюда не пролезает.
_LOGIN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def safe_login(text: str | None) -> str | None:
    """Нормализовать то, что ввёл человек: `CORP\\ivanov` и `ivanov@corp.example` → `ivanov`
    (люди привыкли к обеим формам, а каталог ищет по `sAMAccountName`); регистр — в нижний.
    Не прошло по белому списку — None, снаружи это обычное «неверный логин или пароль»."""
    raw = (text or "").strip()
    if "\\" in raw:
        raw = raw.rsplit("\\", 1)[1]
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    raw = raw.strip().lower()
    return raw if _LOGIN.match(raw) else None


class ProfileStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[tuple[str, str], tuple[float, dict]] = {}

    def path(self, provider: str, login: str) -> Path:
        return self.root / "users" / provider / f"{login}.json"

    def photo_path(self, provider: str, login: str) -> Path | None:
        path = self.path(provider, login).with_suffix(".jpg")
        return path if path.is_file() else None

    def save(self, profile: dict) -> Path:
        provider, login = profile["provider"], profile["login"]
        path = self.path(provider, login)
        path.parent.mkdir(parents=True, exist_ok=True)
        photo = profile.get("photo")
        record = {
            "login": login,
            "provider": provider,
            "name": profile.get("name") or login,
            "given": profile.get("given") or "",
            "surname": profile.get("surname") or "",
            "mail": profile.get("mail") or "",
            "title": profile.get("title") or "",
            "department": profile.get("department") or "",
            "groups": list(profile.get("groups") or []),
            "dn": profile.get("dn") or "",
            "photo": bool(photo),
            "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _write_private(path, json.dumps(record, ensure_ascii=False, indent=2))
        jpg = path.with_suffix(".jpg")
        if photo:
            _write_private(jpg, photo)
        elif jpg.exists():
            jpg.unlink()  # фото из каталога убрали — не показываем старое
        raw = profile.get("raw")
        if raw is not None:
            _write_private(path.with_suffix(".raw.json"), json.dumps(raw, ensure_ascii=False, indent=2))
        self._cache.pop((provider, login), None)
        return path

    def load(self, provider: str, login: str) -> dict | None:
        """Снимок или None (файла нет = сессия отозвана). Кэш по mtime: файл читается раз,
        а не на каждый запрос страницы."""
        path = self.path(provider, login)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._cache.pop((provider, login), None)
            return None
        cached = self._cache.get((provider, login))
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        self._cache[(provider, login)] = (mtime, data)
        return data


def _write_private(path: Path, content: str | bytes) -> None:
    """Файл с правами 0600 — как ключ и журнал: чужим глазам тут делать нечего."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
