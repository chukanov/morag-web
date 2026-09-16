#!/usr/bin/env python3
"""Обращения докладчика к экрану («вот здесь», «на этой схеме», «смотрите на стрелку») и их
разрешение по кадру: что именно он имел в виду.

    python3 tools/screen_refs.py <каталог записи>                   # найти обращения (Instruct)
    python3 tools/screen_refs.py <каталог записи> --resolve          # + спросить Vision по кадру
    python3 tools/screen_refs.py <каталог записи> --resolve --video ~/asr-stack/video/<id>.mp4
    python3 tools/screen_refs.py <каталог записи> --dry              # только предфильтр, без LLM

Зачем. Расшифровка полна указательной речи, референт которой — в кадре: «вот эта стрелочка»,
«в этой колонке», «смотрите, что получилось». Без экрана такие места для поиска пустые. Стадия
достаёт их и приземляет: `resolved` = «[показывает: стрелка от Kafka к Postgres — поток
событий]». Доля разрешённых обращений — метрика всей затеи с видео.

Как. Реплики берутся из `record.words.json` (у каждого слова своё время, поэтому момент
обращения известен до секунды, а не до абзаца). Лексический предфильтр отбирает реплики с
указательными словами — широкий, ради полноты; точность даёт `Instruct`: по окну из
нескольких реплик возвращает JSON со ссылками {реплика, цитата, род, объект}. Род:
`slide` (к слайду в целом), `region` (к месту на экране), `next` (переход к следующему),
`demo` (действие в программе). Момент = первое слово цитаты в реплике; слайд — по шкале
`record.slides.json`. При `--resolve` Vision получает кадр этого момента и цитату.

Выход — `record.refs.json`: {refs: [{t, turn, speaker, quote, kind, object, slide, frame,
resolved}], stats}. Повторный запуск разрешает только неразрешённое.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from describe_slides import (NO_THINK, PROMPT as PROMPT_DESCRIBE, describe, load_env,  # noqa: E402
                             normalize_desc, parse_json, prompt_id)

VERSION = "refs-v1"
MODEL_TEXT = "Instruct"
MODEL_VISION = "Vision"

# Широкий предфильтр: основы слов, а не формы. Цена ложного кандидата — лишний абзац в окне
# LLM; цена пропуска — обращение потеряно. Поэтому широкий.
CUE = re.compile(
    r"(\bвот\b|\bтут\b|\bздесь\b|слайд|экран|схем|картинк|график|таблиц|табличк|стрелк|стрелоч|"
    r"\bвидн|\bвидите|\bвидим\b|смотр|посмотр|обратите внимание|показ|покаж|выдел|подсвеч|"
    r"курсор|кликн|нажм|нажим|окошк|кнопк|строчк|строк[аеиу]\b|колонк|столбц|столбик|"
    r"\bсправа\b|\bслева\b|\bсверху\b|\bснизу\b|\bнаверху\b|\bвнизу\b|\bжёлт|\bкрасн|\bзелён|\bсин[ияе]\b)",
    re.I)

PROMPT_REFS = """\
Ниже реплики из расшифровки доклада (номер, говорящий, текст). Найди места, где говорящий \
ОБРАЩАЕТСЯ К ЭКРАНУ — указывает на слайд, схему, код, окно программы или их часть: «вот здесь», \
«на этой схеме», «смотрите на стрелку», «в этой колонке», «переключаюсь на следующий слайд», \
«сейчас покажу», «нажимаю сюда». Обычные «здесь/тут» без указания на экран («здесь я работаю два \
года») — НЕ обращения.
Для каждого обращения верни:
- "turn": номер реплики,
- "quote": короткая ДОСЛОВНАЯ цитата из реплики (3-12 слов) с указательным словом,
- "kind": "slide" (к слайду в целом), "region" (к месту на экране: блок, стрелка, строка, значение), \
"next" (переход к следующему слайду/окну), "demo" (действие в программе: нажимает, вводит, запускает),
- "object": на что указывает, своими словами по контексту речи (например: «стрелка от сервиса к \
базе», «второй столбец таблицы», «строка с декоратором»); если из речи не ясно — "".
Ответь строго JSON: {"refs": [{"turn": 12, "quote": "...", "kind": "region", "object": "..."}]}. \
Если обращений нет — {"refs": []}.

"""

PROMPT_RESOLVE = """\
Это кадр из видеозаписи доклада в момент, когда докладчик говорит: «{quote}»{obj}.
Что именно на экране он имеет в виду? Ответь одной-двумя фразами, конкретно: назови блок, \
стрелку, строку, значение или элемент интерфейса, о котором речь, и что там написано. \
Людей и окна участников конференции не описывай. Если на кадре нет содержимого экрана — только \
люди или камера — ответь одним словом «люди». Если по кадру понять нельзя — ответь «неясно»."""


# --- реплики -------------------------------------------------------------------------------

def load_turns(record: Path) -> list[dict]:
    w = json.loads((record / "record.words.json").read_text(encoding="utf-8"))
    out = []
    for i, t in enumerate(w["turns"]):
        words = t.get("words") or []
        out.append({"i": i, "speaker": t.get("speaker", ""), "start": t.get("start"), "end": t.get("end"),
                    "words": words, "text": " ".join(x[0] for x in words)})
    return out


def norm_tok(s: str) -> str:
    return re.sub(r"[^\wёЁ]+", "", s.lower()).replace("ё", "е")


def quote_time(turn: dict, quote: str) -> float | None:
    """Время первого слова цитаты: ищем лучшую по совпадению позицию окна той же длины."""
    q = [norm_tok(x) for x in quote.split() if norm_tok(x)]
    ws = [norm_tok(x[0]) for x in turn["words"]]
    if not q or not ws:
        return None
    best, best_i = 0.0, None
    n = len(q)
    for i in range(0, max(1, len(ws) - n + 1)):
        r = SequenceMatcher(None, q, ws[i:i + n]).ratio()
        if r > best:
            best, best_i = r, i
    if best < 0.5 or best_i is None:
        return None
    return float(turn["words"][best_i][1])


# --- LLM ------------------------------------------------------------------------------------

async def chat(client: httpx.AsyncClient, env: dict, model: str, content, max_tokens: int = 1500,
               temperature: float = 0.1) -> tuple[str | None, dict]:
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, **NO_THINK,
            "messages": [{"role": "user", "content": content}]}
    t0 = time.time()
    err = ""
    for attempt in range(3):
        try:
            r = await client.post(env["base_url"] + "/chat/completions", json=body,
                                  headers={"Authorization": f"Bearer {env['api_key']}"})
            r.raise_for_status()
            j = r.json()
            if not isinstance(j, dict):
                # ⚠️ Шлюз под нагрузкой отвечает 200 с телом `null` (ловилось 13.09 на прогоне по
                # корпусу: одно окно из сорока роняло запись целиком). Пустое тело — такой же
                # временный отказ, как обрыв соединения: повтор с паузой.
                raise ValueError(f"пустой ответ шлюза: {r.text[:80]!r}")
            choice = (j.get("choices") or [{}])[0]
            usage = j.get("usage") or {}
            return (choice.get("message") or {}).get("content"), {
                "sec": round(time.time() - t0, 1), "tokens_in": usage.get("prompt_tokens"),
                "tokens_out": usage.get("completion_tokens"), "finish": choice.get("finish_reason")}
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError, OSError) as e:
            # OSError покрывает и ssl.SSLError «DECRYPTION_FAILED_OR_BAD_RECORD_MAC» — сырой TLS-обрыв,
            # который httpx не заворачивает (ловилось 13.09 на лекции со 174 слайдами: одно окно из
            # сорока роняло запись целиком). Временный отказ — повтор.
            err = f"{type(e).__name__}: {str(e)[:160]}"
            await asyncio.sleep(2 * (attempt + 1))
    return None, {"error": err, "sec": round(time.time() - t0, 1)}


WINDOW_WORDS = 400  # ⚠️ Замерено на лекции курса: окно 900 слов — 0 обращений из ≥5 явных
                    # («На слайде представлены виды тестирования»), 400 — 4 по делу, 200 — 6 с
                    # шумом. На длинном окне модель ленится; короче 400 — ловит «ну вот я тут».


def windows(turns: list[dict], max_words: int = WINDOW_WORDS) -> list[list[dict]]:
    """Окна реплик для одного вызова: только реплики с указательными словами, соседние —
    вместе, чтобы «следующий слайд» и ответ на него не разъезжались по вызовам."""
    cand = [t for t in turns if CUE.search(t["text"])]
    out, cur, n = [], [], 0
    for t in cand:
        w = len(t["words"])
        if cur and n + w > max_words:
            out.append(cur)
            cur, n = [], 0
        cur.append(t)
        n += w
    if cur:
        out.append(cur)
    return out


async def find_refs(client, env, turns: list[dict], sem: asyncio.Semaphore, log,
                    window: int = WINDOW_WORDS) -> tuple[list[dict], dict]:
    wins = windows(turns, window)
    stats = {"calls": len(wins), "candidates": sum(len(w) for w in wins), "tokens_in": 0, "tokens_out": 0, "errors": 0}
    refs: list[dict] = []

    async def one(win):
        text = "\n".join(f"[{t['i']}] {t['speaker'] or '—'}: {t['text']}" for t in win)
        out = None
        for attempt in range(2):  # Instruct изредка отвечает не JSON — один повтор
            async with sem:
                content, meta = await chat(client, env, MODEL_TEXT, PROMPT_REFS + text, max_tokens=1500,
                                           temperature=0.1 + 0.2 * attempt)
            stats["tokens_in"] += meta.get("tokens_in") or 0
            stats["tokens_out"] += meta.get("tokens_out") or 0
            out = parse_json(content) if content else None
            if out is not None:
                break
        if out is None:
            stats["errors"] += 1
            log(f"  ✗ окно [{win[0]['i']}..{win[-1]['i']}]: {meta.get('error') or 'не JSON'}")
            return
        by_i = {t["i"]: t for t in win}
        for r in out.get("refs") or []:
            try:
                ti = int(r.get("turn"))
            except (TypeError, ValueError):
                continue
            t = by_i.get(ti)
            if not t:
                continue
            kind = str(r.get("kind") or "").strip().lower()
            if kind not in ("slide", "region", "next", "demo"):
                continue
            quote = str(r.get("quote") or "").strip()
            at = quote_time(t, quote)
            refs.append({"turn": ti, "speaker": t["speaker"], "t": round(at if at is not None else t["start"], 2),
                         "t_exact": at is not None, "quote": quote, "kind": kind,
                         "object": str(r.get("object") or "").strip()})

    await asyncio.gather(*(one(w) for w in wins))
    refs.sort(key=lambda r: r["t"])
    return refs, stats


# --- связка со шкалой и разрешение --------------------------------------------------------

def attach_slides(refs: list[dict], slides: dict | None) -> None:
    """Слайд/выборка, показанные в момент обращения. Слайд — по [t0, t1); иначе ближайшая
    выборка из активного участка, если она в пределах половины шага выборки."""
    if not slides:
        return
    every = slides.get("params", {}).get("active_every", 60)
    for r in refs:
        t = r["t"]
        if r["kind"] == "next":
            # «на следующем слайде» — речь о том, что ПОЯВИТСЯ: берём первый слайд после момента
            hit = next((s for s in slides.get("slides", []) if s["t0"] > t), None)
        else:
            hit = next((s for s in slides.get("slides", []) if s["t0"] <= t < s["t1"] + 1.0), None)
        if hit:
            r["slide"] = hit["n"]
            if hit.get("frame"):
                r["frame"] = hit["frame"]
            if hit.get("people"):
                r["people"] = True  # кадр момента — люди; модель его уже видела, кадр удалён
            continue
        near = min(slides.get("samples", []), key=lambda s: abs(s["t"] - t), default=None)
        if near and abs(near["t"] - t) <= every / 2 + 1:
            if near.get("frame"):
                r["sample"] = near["n"]
                r["frame"] = near["frame"]
            elif near.get("people"):
                r["people"] = True


def frame_for(record: Path, r: dict, video: Path | None, slides: dict | None) -> Path | None:
    """Кадр момента: из шкалы, а если его нет и есть видео — вырезать в refs/<t>.jpg."""
    if r.get("frame") and (record / r["frame"]).is_file():
        return record / r["frame"]
    if not video or not slides:
        return None
    from slides_from_video import Params, save_frame
    bb = slides["layout"]["bbox"]
    crop = f"crop=iw*{bb[2] - bb[0]:.4f}:ih*{bb[3] - bb[1]:.4f}:iw*{bb[0]:.4f}:ih*{bb[1]:.4f}"
    out = record / "refs" / f"t{int(r['t']):05d}.jpg"
    out.parent.mkdir(exist_ok=True)
    if out.is_file() or save_frame(video, r["t"], out, crop, Params().frame_width):
        r["frame"] = f"refs/{out.name}"
        return out
    return None


async def resolve_refs(client, env, record: Path, refs: list[dict], video: Path | None, slides: dict | None,
                       sem: asyncio.Semaphore, log) -> dict:
    stats = {"asked": 0, "resolved": 0, "no_frame": 0, "unclear": 0, "people": 0, "tokens_in": 0, "tokens_out": 0}
    pid = prompt_id(PROMPT_RESOLVE, MODEL_VISION)

    async def one(r):
        if r.get("resolve_meta", {}).get("prompt") == pid or r.get("people"):
            return
        fresh = not (r.get("frame") and (record / r["frame"]).is_file())
        img = frame_for(record, r, video, slides)
        if img is None:
            stats["no_frame"] += 1
            return
        if fresh:
            # ⚠️ Кадр вырезан только что, модель его ещё не видела: сначала «что это»,
            # тем же промптом, что у слайдов. На вопрос «что он имеет в виду» модель
            # описывает и лицо в камере — так кадр с человеком остался на диске (ловилось).
            out, _ = await describe(client, env, img, MODEL_VISION, PROMPT_DESCRIBE, sem)
            kind = normalize_desc(out)["kind"] if out else "other"
            if kind == "people":
                img.unlink(missing_ok=True)
                r.pop("frame", None)
                r["people"] = True
                stats["people"] += 1
                return
        obj = f" (по контексту речь о: {r['object']})" if r.get("object") else ""
        prompt = PROMPT_RESOLVE.format(quote=r["quote"], obj=obj)
        b64 = base64.b64encode(img.read_bytes()).decode()
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
        async with sem:
            text, meta = await chat(client, env, MODEL_VISION, content, max_tokens=400)
        stats["asked"] += 1
        stats["tokens_in"] += meta.get("tokens_in") or 0
        stats["tokens_out"] += meta.get("tokens_out") or 0
        if not text:
            r["resolve_error"] = meta.get("error") or "пусто"
            return
        text = text.strip().strip("`").strip()
        r["resolve_meta"] = {"model": MODEL_VISION, "prompt": pid, "sec": meta.get("sec")}
        if re.match(r"^\W*люди", text, re.I):
            # ⚠️ кадр момента — люди: не храним (вырезанный сюда кадр — прочь), не описываем
            r["people"] = True
            r.pop("resolved", None)
            if r.get("frame", "").startswith("refs/"):
                (record / r["frame"]).unlink(missing_ok=True)
                r.pop("frame", None)
            stats["people"] += 1
            return
        r["resolved"] = text
        if re.match(r"^\W*неясно", text, re.I) or len(text) < 8:
            stats["unclear"] += 1
        else:
            stats["resolved"] += 1
        log(f"  {r['t']:7.1f}s {r['kind']:6s} «{r['quote'][:40]}» → {text[:90]!r}")

    await asyncio.gather(*(one(r) for r in refs))
    # сироты прежних прогонов в refs/ — прочь: файл, на который никто не ссылается, мог быть
    # вырезан до того, как научились отсеивать людей
    live = {r.get("frame") for r in refs}
    for f in (record / "refs").glob("*.jpg") if (record / "refs").is_dir() else []:
        if f"refs/{f.name}" not in live:
            f.unlink()
    return stats


async def run(record: Path, a) -> int:
    turns = load_turns(record)
    slides_path = record / "record.slides.json"
    slides = json.loads(slides_path.read_text(encoding="utf-8")) if slides_path.is_file() else None
    out_path = record / "record.refs.json"
    prev = json.loads(out_path.read_text(encoding="utf-8")) if out_path.is_file() and not a.redo else None
    cand = [t for t in turns if CUE.search(t["text"])]
    print(f"{record.name}: реплик {len(turns)}, с указательными словами {len(cand)}, "
          f"окон для LLM {len(windows(turns, a.window))}")
    if a.dry:
        return 0
    env = load_env()
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
        if prev and prev.get("refs"):
            refs, stats = prev["refs"], prev.get("stats", {})
            print(f"  обращений из прошлого прогона: {len(refs)}")
        else:
            refs, stats = await find_refs(client, env, turns, sem, print, a.window)
            print(f"  обращений найдено {len(refs)} за {stats['calls']} вызовов "
                  f"(токенов {stats['tokens_in']}+{stats['tokens_out']}, ошибок {stats['errors']})")
            from collections import Counter
            print("  по родам:", dict(Counter(r["kind"] for r in refs)),
                  "· время по словам:", sum(r["t_exact"] for r in refs), "из", len(refs))
        attach_slides(refs, slides)
        data = {"version": VERSION, "model": MODEL_TEXT, "prompt": prompt_id(PROMPT_REFS, MODEL_TEXT),
                "refs": refs, "stats": stats}
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        if a.resolve:
            rs = await resolve_refs(client, env, record, refs, a.video, slides, sem, print)
            data["stats"]["resolve"] = rs
            print(f"  разрешение: спрошено {rs['asked']}, ответ по существу {rs['resolved']}, "
                  f"«неясно» {rs['unclear']}, люди {rs['people']}, без кадра {rs['no_frame']}; "
                  f"токенов {rs['tokens_in']}+{rs['tokens_out']}")
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"→ {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", type=Path)
    ap.add_argument("--resolve", action="store_true", help="спросить Vision по кадру для каждого обращения")
    ap.add_argument("--video", help="видео — вырезать кадры моментов, которых нет в шкале (с --remote: путь на сервере)")
    ap.add_argument("--remote", metavar="HOST", help="видео лежит на сервере (user@host): кадры режет ffmpeg по ssh")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--window", type=int, default=WINDOW_WORDS, help="слов реплик на один вызов Instruct")
    ap.add_argument("--redo", action="store_true", help="искать обращения заново, а не брать из record.refs.json")
    ap.add_argument("--dry", action="store_true", help="только предфильтр, без вызовов")
    a = ap.parse_args()
    if a.video:
        from slides_from_video import Source
        a.video = Source(a.video, a.remote)   # локальный файл или файл на сервере — save_frame различает сам
    return asyncio.run(run(a.record, a))


if __name__ == "__main__":
    sys.exit(main())
