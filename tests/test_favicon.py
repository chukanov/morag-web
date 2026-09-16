"""Значок вкладки — из бренда корпуса, иначе общий из статики (16.09).

Путь `/favicon.svg` исторический: под ним может лежать PNG (у корпуса значок — растровый, как
у сайта-источника), браузер смотрит на Content-Type. Файл живёт у корпуса, а не в `web/`:
фронт generic и однажды уедет в публичный репозиторий — лицу там не место.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.main import app  # noqa: E402


def test_без_бренда_общий_svg():
    with TestClient(app) as c:
        r = c.get("/favicon.svg")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert b"<svg" in r.content


def test_значок_из_бренда_витрины(tmp_path):
    (tmp_path / "f.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    with TestClient(app) as c:
        app.state.hub = types.SimpleNamespace(brand={"favicon": "f.png"}, brand_dir=tmp_path)
        try:
            r = c.get("/favicon.svg")
            assert r.status_code == 200 and r.headers["content-type"] == "image/png"
            assert r.content.startswith(b"\x89PNG")
            # ⓘ Cache-Control здесь перебивает middleware `no_store_for_static` (всё вне /api/):
            # значок не кэшируется вовсе, как и остальная статика; 19 КБ на страницу — терпимо.
            # Имя не выводит из каталога бренда.
            app.state.hub.brand["favicon"] = "../f.png"
            assert c.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
        finally:
            app.state.hub = None
