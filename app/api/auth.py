"""Вход, выход, «кто я». Логика — в `app/auth/service.py`; здесь только HTTP.

Тексты ошибок входа нарочно одинаковые для «нет такого» и «неверный пароль» — иначе форма
становится справочником учёток. Каталог недоступен — 503, чтобы человек не решил, что
ошибся паролем. Лимит попыток — 429 с `Retry-After`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..auth.local import BadCredentials
from ..auth.ldap import LdapUnavailable
from ..auth.service import Forbidden, Throttled
from .ask import _client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger(__name__)

BAD = "неверный логин или пароль"


class Login(BaseModel):
    login: str = Field(max_length=128)
    password: str = Field(max_length=512)


def _auth(request: Request):
    return request.app.state.auth


def _title(request: Request) -> str:
    state = request.app.state
    hub = getattr(state, "hub", None)
    if hub is not None and hub.brand.get("title"):
        return hub.brand["title"]
    corpus = getattr(state, "default_corpus", None)
    if corpus is not None and (corpus.brand or {}).get("title"):
        return corpus.brand["title"]
    return "Записи"


def _home(request: Request) -> str:
    """«Домашний» адрес сайта — куда возвращать после входа, когда возвращать больше некуда.

    Не `/`: на общем с чужим сайтом домене корень — чужой, и «вышел → вошёл» уводило бы
    туда. У сайта с одним пространством дом — само пространство (`/demo`); витрина из
    нескольких — корень.
    """
    state = request.app.state
    corpora = getattr(state, "corpora", {}) or {}
    if getattr(state, "hub", None) is not None and len(corpora) > 1:
        return "/"
    corpus = getattr(state, "default_corpus", None)
    return f"/{corpus.slug}" if corpus is not None else "/"


def _brand(request: Request) -> dict:
    """Знак и слово для шапки формы входа: у витрины, иначе у пространства по умолчанию.
    Форма живёт без сессии и `/api/site` не читает — знак приезжает отсюда."""
    state = request.app.state
    for holder in (getattr(state, "hub", None), getattr(state, "default_corpus", None)):
        if holder is None:
            continue
        pub = holder.public()
        brand = pub.get("brand") if isinstance(pub.get("brand"), dict) else pub
        if brand.get("mark") or brand.get("wordmark"):
            return {"mark": brand.get("mark"), "wordmark": brand.get("wordmark") or ""}
    return {"mark": None, "wordmark": ""}


def _me(request: Request) -> dict:
    auth = _auth(request)
    editing = request.app.state.cfg.editing.enabled
    user = auth.user_of(request)
    body = user.public() if user else {"login": None, "name": "", "provider": None, "role": None,
                                        "title": "", "department": "", "photo": False,
                                        "speaker_name": ""}
    body["can"] = auth.capabilities(request, editing, request.app.state.cfg.ingest.enabled)
    body["home"] = _home(request)
    return body


@router.get("/state")
async def state(request: Request) -> dict:
    """Открытая ручка для формы входа: включена ли авторизация, какие провайдеры, как
    называется сайт, и не вошёл ли человек уже (тогда форма сразу отправит его дальше)."""
    auth = _auth(request)
    return {"enabled": auth.enabled, "providers": auth.providers() if auth.enabled else [],
            "title": _title(request), "logged_in": auth.user_of(request) is not None,
            "home": _home(request), "brand": _brand(request)}


@router.post("/login")
async def login(request: Request, payload: Login) -> JSONResponse:
    auth = _auth(request)
    if not auth.enabled:
        raise HTTPException(404, "вход выключен")
    cfg = request.app.state.cfg
    ip = _client_ip(request, cfg.server.trusted_proxy_hops)
    try:
        # В поток: каталог ходит по сети блокирующе, а рядом идут SSE-ответы другим людям.
        identity = await asyncio.to_thread(auth.login, payload.login, payload.password, ip)
    except Throttled as limited:
        raise HTTPException(429, "слишком много попыток — подождите",
                            headers={"Retry-After": str(limited.retry_after)}) from None
    except BadCredentials:
        raise HTTPException(401, BAD) from None
    except Forbidden as denied:
        # Пароль верный — так и говорим: человек не должен перебирать пароль, которого не знает.
        raise HTTPException(403, str(denied) or "нет доступа к сайту") from None
    except LdapUnavailable:
        raise HTTPException(503, "каталог недоступен — попробуйте позже") from None
    request.state.user = identity
    response = JSONResponse(_me(request))
    auth.issue_cookie(response, identity, auth.secure_for(request, cfg.server.trusted_proxy_hops))
    return response


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    _auth(request).clear_cookie(response)
    return response


@router.get("/me")
async def me(request: Request) -> dict:
    """Кто вошёл и что ему можно. При выключенной авторизации — та же форма с пустой
    личностью, чтобы у фронта был один путь."""
    return _me(request)


@router.get("/me/photo")
async def photo(request: Request):
    auth = _auth(request)
    user = auth.user_of(request)
    path = auth.store.photo_path(user.provider, user.login) if user else None
    if path is None:
        raise HTTPException(404, "фото нет")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
