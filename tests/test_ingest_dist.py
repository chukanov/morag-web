"""Раздача установщика с сайта: зеркало, пропуск, строка установки (`app/content/dist.py`).

Почему это закрепляем тестом, а не «посмотрели глазами»:
  * **пропуск открывает файлы БЕЗ сессии** — значит подделка пропуска это дыра, а просроченный
    пропуск обязан отказывать, а не отдавать;
  * **имя файла приезжает из адреса** — классическое место для `../../`;
  * строка установки уходит человеку в терминал: остались подстановки `@SITE@` — он поставит
    ничего, и поймёт это через полчаса;
  * зеркала может не быть вовсе (публичная платформа) — тогда 404 на всём, а не пятисотка.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.content import dist  # noqa: E402

SECRET = b"s" * 32


# --- пропуск ---------------------------------------------------------------------------

def test_pass_is_short_signed_and_dies_of_old_age():
    token = dist.issue(SECRET)
    assert len(token) < 40, "пропуск человек копирует вместе с командой — длинный читается как шум"
    assert dist.valid(token, SECRET)
    assert not dist.valid(token, b"x" * 32), "подписан другим ключом — не наш"
    assert not dist.valid(token[:-1] + ("A" if token[-1] != "A" else "B"), SECRET), "подпись правится"
    assert not dist.valid("", SECRET) and not dist.valid("мусор", SECRET)
    assert not dist.valid(token, SECRET, now=time.time() + dist.TTL + 120), "неделя прошла — отказ"
    assert dist.valid(dist.issue(SECRET, ttl=60, now=time.time()), SECRET)


def test_pass_key_is_not_the_session_key(tmp_path):
    """⚠️ Ключ пропуска ВЫВЕДЕН из ключа сессий, а не равен ему: иначе одной подписью можно было
    бы притвориться другой. И без входа ключ всё равно есть — иначе раздача падала бы."""
    from app.auth.service import AuthService
    from app.config import AuthCfg

    service = AuthService(AuthCfg(enabled=True, secret="секрет"), tmp_path)
    assert service.derived_secret(dist.PURPOSE) != "секрет".encode("utf-8")
    assert service.derived_secret(dist.PURPOSE) != service.derived_secret("что-то другое")
    off = AuthService(AuthCfg(enabled=False), tmp_path)
    assert len(off.derived_secret(dist.PURPOSE)) == 32, "вход выключен — ключ на время процесса"


# --- каталог зеркала -------------------------------------------------------------------

def mirror(root: Path, *, missing: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "python.tar.gz").write_bytes(b"P" * 100)
    (root / "tools.tar.gz").write_bytes(b"T" * 50)
    files = [{"name": "python.tar.gz", "unpack": "targz", "to": "root/", "bytes": 1, "sha256": "x", "title": "питон"},
             {"name": "tools.tar.gz", "unpack": "targz", "to": "root/web/", "bytes": 1, "sha256": "y", "title": "инструменты"}]
    if missing:
        files.append({"name": "model-whisper.tar", "unpack": "tar", "to": "stack/models/",
                      "bytes": 1, "sha256": "z", "title": "модель расшифровки"})
    (root / "manifest.json").write_text(json.dumps({"built": "2026-09-23 21:46", "files": files}), encoding="utf-8")
    return root


def test_catalog_measures_the_disk_not_the_manifest(tmp_path):
    data = dist.catalog(mirror(tmp_path / "d", missing=True))
    by = {f["name"]: f for f in data["files"]}
    assert by["python.tar.gz"]["bytes"] == 100, "размер — с диска: доставку могло оборвать"
    assert by["model-whisper.tar"]["missing"] is True, "файла нет — установщик должен узнать это заранее"
    assert data["bytes"] == 150 and data["built"] == "2026-09-23 21:46"
    assert dist.catalog(tmp_path / "нет-такого") == {}


def test_file_of_refuses_everything_but_a_plain_name(tmp_path):
    root = mirror(tmp_path / "d")
    (root / "внутри").mkdir()
    (root / "внутри" / "секрет").write_text("х", encoding="utf-8")
    assert dist.file_of(root, "python.tar.gz") is not None
    for bad in ("../manifest.json", "внутри/секрет", "/etc/passwd", ".hidden", "", "имя с пробелом",
                "a" * 81, "python.tar.gz/"):
        assert dist.file_of(root, bad) is None, bad


# --- живые ручки -----------------------------------------------------------------------

@pytest.fixture
def live(tmp_path, monkeypatch):
    """Сайт на копии демо-корпуса с настроенным зеркалом; вход выключен (рубеж — петля)."""
    demo = tmp_path / "demo"
    shutil.copytree(REPO / "corpora" / "demo", demo)
    monkeypatch.setenv("MORAG_WEB_CORPUS", str(demo))
    monkeypatch.setenv("MORAG_WEB_CONFIG", str(REPO / "app" / "config.example.yml"))
    from app.main import app
    with TestClient(app) as c:
        cfg = app.state.cfg.ingest
        cfg.enabled, cfg.dist_dir = True, str(mirror(tmp_path / "dist"))
        app.state.cfg.editing.local_only = False
        try:
            yield c, app
        finally:
            cfg.enabled, cfg.dist_dir = False, ""


def pass_of(c) -> str:
    return c.get("/api/ingest/dist").json()["install"].split("/get/")[1].split("/")[0]


def test_page_gets_the_line_and_the_sizes(live):
    c, _ = live
    body = c.get("/api/ingest/dist").json()
    assert body["bytes"] == 150 and body["days"] == 7
    assert body["install"].startswith("/usr/bin/curl -fsSL http://testserver/api/ingest/get/"), (
        "системный curl: он верит связке ключей машины, а curl из conda/brew — только публичным корням")
    assert body["install"].endswith("/install | sh")
    assert [f["title"] for f in body["files"]] == ["питон", "инструменты"]
    assert all("sha256" not in f for f in body["files"]), "контрольные суммы странице не нужны"


def test_the_line_says_https_when_the_proxy_says_so(live):
    """⚠️ За apache схема соединения — http; отдать её в команду значит отправить `curl` туда,
    где сервер ответит редиректом, а `| sh` выполнит пустоту."""
    c, app = live
    app.state.cfg.server.trusted_proxy_hops = 1
    body = c.get("/api/ingest/dist", headers={"X-Forwarded-Proto": "https", "Host": "site.example.org"}).json()
    assert body["install"].startswith("/usr/bin/curl -fsSL https://site.example.org/api/ingest/")


def test_installer_comes_out_substituted(live):
    c, _ = live
    token = pass_of(c)
    text = c.get(f"/api/ingest/get/{token}/install").text
    assert "@SITE@" not in text and "@TOKEN@" not in text, "неподставленный установщик ставит ничего"
    assert "http://testserver" in text and token in text
    assert text.startswith("#!/bin/sh")


def test_mirror_file_is_given_by_pass_with_ranges(live):
    c, _ = live
    token = pass_of(c)
    whole = c.get(f"/api/ingest/get/{token}/file/python.tar.gz")
    assert whole.status_code == 200 and whole.content == b"P" * 100
    part = c.get(f"/api/ingest/get/{token}/file/python.tar.gz", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == b"P" * 10, "докачка: полтора гигабайта рвутся"


def test_a_bad_pass_opens_nothing(live):
    c, _ = live
    for bad in ("aaaa.bbbb", "", "%2e%2e"):
        assert c.get(f"/api/ingest/get/{bad}/file/python.tar.gz").status_code in (403, 404)
        assert c.get(f"/api/ingest/get/{bad}/install").status_code in (403, 404)
    token = pass_of(c)
    assert c.get(f"/api/ingest/get/{token}/file/manifest.json").status_code == 200
    assert c.get(f"/api/ingest/get/{token}/file/нет-такого").status_code == 404


def test_zip_holds_one_runnable_command(live):
    c, _ = live
    body = c.get("/api/ingest/app.zip")
    assert body.status_code == 200 and body.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(__import__("io").BytesIO(body.content)) as zf:
        (info,) = zf.infolist()
        assert info.filename.endswith(".command"), "двойной щелчок по .command открывает Терминал"
        assert info.external_attr >> 16 & 0o111, "без бита запуска Finder откроет файл текстом"
        text = zf.read(info).decode("utf-8")
    assert text.startswith("#!/bin/sh") and "/api/ingest/get/" in text and "| sh" in text


def test_without_a_mirror_everything_is_404(live):
    """Публичная платформа раздачи не держит — и это не ошибка сервера, а «здесь такого нет»."""
    c, app = live
    app.state.cfg.ingest.dist_dir = ""
    assert c.get("/api/ingest/dist").status_code == 404
    assert c.get("/api/ingest/app.zip").status_code == 404
    assert c.get("/api/ingest/get/что-угодно/install").status_code == 404


def test_pass_paths_are_open_at_the_gate_and_the_rest_is_not():
    """Гейт входа пускает `/api/ingest/get/…` без сессии НАМЕРЕННО (у `curl` её нет), а страницу
    раздачи и zip — нет. Разъедется — либо установка не работает, либо зеркало открыто всем."""
    from app.auth.service import is_public
    assert is_public("/api/ingest/get/abc.def/install")
    assert is_public("/api/ingest/get/abc.def/file/python.tar.gz")
    assert not is_public("/api/ingest/dist")
    assert not is_public("/api/ingest/app.zip")
    assert not is_public("/api/ingest/options")
