"""Шлюз LLM через сайт (`app/api/llm.py`): ключ у коллеги не спрашиваем вовсе.

Почему это стоит закреплять тестом, а не «посмотрели глазами»:
  * рубеж тут — САМА СЕССИЯ, пришедшая строкой в `Authorization`; ошибись в разборе, и ручка
    либо откроется всем, либо не откроется никому (а выглядит это как «расшифровка падает»);
  * наружу мы ходим КЛЮЧОМ КОРПУСА — он не должен протекать в ответ и должен подставляться
    вместо того, что прислал клиент;
  * «снимок учётки удалили» обязано означать «доступа нет» — на этом держится вся отзываемость;
  * расход виден в журнале: иначе о лишнем мы узнаем от шлюза, а не от себя.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

BODY = {"model": "Instruct", "messages": [{"role": "user", "content": "глоссарий"}]}


@pytest.fixture
def live(tmp_path, monkeypatch):
    """Сайт с включённым входом, локальным пользователем-редактором и шлюзом-заглушкой."""
    from app.auth.local import hash_password

    demo = tmp_path / "demo"
    shutil.copytree(REPO / "corpora" / "demo", demo)
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "corpora:\n"
        f"  - dir: {demo}\n"
        "upload:\n  enabled: true\n"
        "topic:\n  base_url: https://llm.example.org/api\n  model: Instruct\n  api_key: corpus-key-xyz\n"
        "journal:\n  enabled: true\n  path: ./journal.jsonl\n"
        "auth:\n  enabled: true\n  secret: секрет-теста\n"
        f"  data_dir: {tmp_path / 'authdata'}\n"
        "  users:\n"
        f"    - login: kuznetsova\n      password: \"{hash_password('пароль')}\"\n"
        "      name: Мария Кузнецова\n      role: editor\n",
        encoding="utf-8")
    monkeypatch.setenv("MORAG_WEB_CONFIG", str(cfg))
    monkeypatch.setenv("MORAG_WEB_CORPUS", str(demo))

    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Instruct"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ответ шлюза"}}]})

    import app.api.llm as llm_api

    real = httpx.AsyncClient
    monkeypatch.setattr(llm_api.httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(gateway)}))

    from app.main import app
    with TestClient(app) as c:
        yield c, seen, tmp_path


def token_of(c: TestClient) -> str:
    """Строка сессии — ровно то, что приложение положит в файл стека как `OR_KEY`."""
    r = c.post("/api/auth/login", json={"login": "kuznetsova", "password": "пароль"})
    assert r.status_code == 200, r.text
    name = [k for k in c.cookies if "session" in k][0]
    token = c.cookies[name]
    c.cookies.clear()          # дальше ходим КАК СТЕК: без cookie, только заголовком
    return token


def test_the_session_string_works_as_the_gateway_credential(live):
    c, seen, _ = live
    token = token_of(c)
    r = c.post("/api/upload/llm/chat/completions", json=BODY, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "ответ шлюза"
    sent = seen[-1]
    assert str(sent.url) == "https://llm.example.org/api/chat/completions"
    assert sent.headers["authorization"] == "Bearer corpus-key-xyz", "наружу — ключ корпуса, а не то, что прислали"
    assert "corpus-key-xyz" not in r.text, "ключ не протекает в ответ"


def test_options_tell_the_app_what_to_write_into_the_stack(live):
    c, _, _ = live
    c.post("/api/auth/login", json={"login": "kuznetsova", "password": "пароль"})
    llm = c.get("/api/upload/options").json()["llm"]
    assert llm["via_site"] is True and llm["path"] == "/api/upload/llm"
    assert llm["model"] == "Instruct" and "session" in llm["cookie"]


def test_without_a_session_nobody_gets_in(live):
    c, seen, _ = live
    token = token_of(c)
    before = len(seen)
    for bad in ("", "not-a-session", token[:-2] + ("A" if token[-1] != "A" else "B")):
        head = {"Authorization": f"Bearer {bad}"} if bad else {}
        assert c.post("/api/upload/llm/chat/completions", json=BODY, headers=head).status_code == 401
    assert len(seen) == before, "до шлюза чужой запрос не доходит вовсе"


def test_deleting_the_profile_revokes_it_at_once(live):
    """⚠️ Главное свойство «сессии вместо ключа»: отозвать = удалить снимок учётки, как и
    «разлогинить везде». Без этого пришлось бы заводить свой список отозванных токенов."""
    c, _, tmp_path = live
    token = token_of(c)
    head = {"Authorization": f"Bearer {token}"}
    assert c.post("/api/upload/llm/chat/completions", json=BODY, headers=head).status_code == 200
    for snapshot in (tmp_path / "authdata" / "users").rglob("*.json"):
        snapshot.unlink()
    assert c.post("/api/upload/llm/chat/completions", json=BODY, headers=head).status_code == 401


def test_a_viewer_is_not_allowed(live):
    """Право то же, что у загрузки записи (`upload` → editor): смотрящему шлюз не нужен."""
    c, _, _ = live
    token = token_of(c)
    c.app.state.cfg.auth.users[0].role = "viewer"
    r = c.post("/api/upload/llm/chat/completions", json=BODY, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_every_call_leaves_a_line_in_the_journal(live):
    c, _, tmp_path = live
    token = token_of(c)
    c.post("/api/upload/llm/chat/completions", json=BODY, headers={"Authorization": f"Bearer {token}"})
    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").strip().splitlines()
    row = [__import__("json").loads(x) for x in lines if '"upload-llm"' in x][-1]
    assert row["user"] == "kuznetsova" and row["status"] == 200 and row["path"] == "/chat/completions"
    assert row["in"] > 0 and "ms" in row


def test_the_proxy_is_off_when_upload_is_off(live):
    c, _, _ = live
    token = token_of(c)
    head = {"Authorization": f"Bearer {token}"}
    c.app.state.cfg.upload.llm.enabled = False
    assert c.post("/api/upload/llm/chat/completions", json=BODY, headers=head).status_code == 404
    c.app.state.cfg.upload.llm.enabled = True
    c.app.state.cfg.upload.enabled = False
    assert c.post("/api/upload/llm/chat/completions", json=BODY, headers=head).status_code == 404
    c.app.state.cfg.upload.enabled = True


def test_a_huge_body_is_refused_before_the_gateway(live):
    """Кадр Vision в base64 — сотни килобайт; мегабайты означают ошибку, и память сайта на них
    тратить незачем."""
    c, seen, _ = live
    token = token_of(c)
    c.app.state.cfg.upload.llm.max_mb = 0.01
    before = len(seen)
    r = c.post("/api/upload/llm/chat/completions", json={"model": "Vision", "messages": ["x" * 20000]},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 413 and len(seen) == before
