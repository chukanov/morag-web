#!/usr/bin/env python3
"""Описать ключевые кадры записи Vision-моделью шлюза и записать в `record.slides.json`.

    python3 tools/describe_slides.py <каталог записи>            # всё неописанное
    python3 tools/describe_slides.py <каталог записи> --limit 5 --dry
    python3 tools/describe_slides.py <каталог записи> --redo     # заново, поверх старого

Вход — `record.slides.json` и `slides/*.jpg` от `slides_from_video.py`. Модель — `Vision` на
шлюзе (`ASR_LLM_BASE_URL`, `OR_KEY` из файла стека, см. `stack_env_file`), OpenAI-совместимый
`chat/completions` с картинкой в `image_url`. Замерено 12.09.2026: кадр 1280 px ≈ 900 токенов,
ответ 2.9 с, текст слайда с кодом прочитан символ в символ.

Что пишется в каждый слайд/выборку: `desc` = {kind, title, text, visual, unreadable} и
`desc_meta` = {model, prompt, at} — отпечаток, по которому видно, каким промптом и какой
моделью описано (тот же принцип, что `x_enriched.env` у расшифровки). Повторный запуск
описывает только то, у чего отпечатка нет или он другой.

⚠️ Люди не описываются и НЕ ХРАНЯТСЯ: если модель отвечает `kind: people`, кадр удаляется с
диска, в сайдкаре остаётся только пометка. Камера и галерея лиц — персональные данные, а
«не описали» ≠ «не сохранили».

Второй ярус тождества — по тексту: слайды с одинаковыми `title`+`text` получают один `slide`
(Jitsi/Meet перерисовывают область показа, редактор PowerPoint рисует тот же слайд в рамке,
уведомление всплывает поверх — пиксели разные, слайд тот же).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

PROMPT = """\
Это кадр из видеозаписи доклада или лекции: на экране слайд презентации, окно программы, код, \
терминал, браузер или люди в видеоконференции. Опиши СОДЕРЖИМОЕ ЭКРАНА. Людей, лица, окна \
участников конференции не описывай — если на кадре только люди, ответь kind "people" и пустые поля.
Ответь строго одним JSON-объектом без пояснений:
{"kind": "slide | code | app | terminal | browser | diagram | people | other",
 "title": "заголовок слайда или окна, если есть",
 "text": "весь текст на экране ДОСЛОВНО, построчно, как написан (код — как код, таблица — строками)",
 "visual": "смысловая графика помимо текста: схема (какие блоки и стрелки между ними), график (оси, что растёт/падает), таблица (о чём), скриншот (какая программа, что в ней видно). Оформление — фон, узор, маркеры списка, логотип шаблона — НЕ описывай; если только текст — пустая строка",
 "unreadable": false}
Если текст не читается (размыт, мелкий) — ставь "unreadable": true и не выдумывай."""

# Второй вопрос — только когда на кадре люди: КТО это. Докладчик — лицо доклада, его кадр
# хранится и может стать обложкой (владелец, 14.09: «обычно принято вставлять фотографии
# докладчика»); зал и галерея участников — чужие лица, кадр удаляется, как и раньше.
# Отдельным вызовом, а не полем основного промпта: смена основного промпта меняет его отпечаток
# и заставила бы описать заново все 5.7 тысяч кадров корпуса.
PEOPLE_PROMPT = """\
На этом кадре люди. Ответь строго одним JSON-объектом без пояснений — кто это:
{"who": "speaker | audience | gallery | mixed",
 "setting": "stage | webcam | other",
 "count": сколько людей в кадре}
speaker — один человек ведёт доклад: стоит на сцене у экрана или говорит в камеру крупным планом; \
audience — зал, слушатели; gallery — плитка окон участников видеоконференции; mixed — докладчик \
вместе с залом или галереей. setting: stage — сцена или аудитория, webcam — окно камеры, other — иное."""
WHO = {"speaker", "audience", "gallery", "mixed"}
SETTING = {"stage", "webcam", "other"}

MODEL = "Vision"
CONCURRENCY = 3
# ⚠️ Thinking выключаем ЯВНО, тремя способами сразу (как клиент морага — каждый провайдер
# читает свой). Замерено 12.09: без этого Qwen на свободный вопрос «что он имеет в виду»
# тратит ВЕСЬ max_tokens на рассуждение и отдаёт пустой content — 21 пустой ответ из 21;
# на JSON-описании — 7 пустых из 40.
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none",
            "reasoning": {"effort": "none"}}


def stack_env_file() -> Path:
    """Файл стека транскрибации с адресом и ключом шлюза: `$ASR_STACK_ENV` (тот же ключ, что у
    `stack.sh` морага), иначе из `ops.env` корпуса, иначе `~/.asr-stack.env`."""
    import spaces  # noqa: PLC0415
    raw = os.environ.get("ASR_STACK_ENV") or spaces.ops_env().get("ASR_STACK_ENV") or "~/.asr-stack.env"
    return Path(raw).expanduser()


def load_env() -> dict:
    """Адрес и ключ шлюза из файла стека; прокси оболочки снимаем — контуру он не нужен, а
    питон через него падает на сертификате.

    ⚠️⚠️ Здесь же включаем доверие СВЯЗКЕ КЛЮЧЕЙ МАШИНЫ (`truststore`). Сайт и шлюз подписаны
    внутренним центром сертификации, а httpx верит только `certifi` — без этого ВСЕ кадры падают
    с `CERTIFICATE_VERIFY_FAILED` (ловилось живьём 24.09). В `upload.py` такая же инъекция есть, но она
    живёт В СВОЁМ ПРОЦЕССЕ и в подпроцессы не наследуется; `SSL_CERT_FILE` тоже не спасает — его
    экспортирует только враппер установщика, а из чекаута его нет вовсе. `screen_refs.py` берёт
    эту же функцию — чинится разом.
    """
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(var, None)
    try:
        import truststore   # noqa: PLC0415 - нужен только здесь и только один раз
        truststore.inject_into_ssl()
    except Exception:       # noqa: BLE001 - нет пакета — работаем как раньше, через certifi
        pass
    env_file = stack_env_file()
    values: dict[str, str] = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.replace("export ", "").strip()] = val.strip().strip('"').strip("'")
    base = os.environ.get("ASR_LLM_BASE_URL") or values.get("ASR_LLM_BASE_URL") or ""
    key = os.environ.get("OR_KEY") or values.get("OR_KEY") or ""
    if not (base and key):
        sys.exit(f"нет адреса/ключа шлюза: ASR_LLM_BASE_URL и OR_KEY в {env_file}")
    return {"base_url": base.rstrip("/"), "api_key": key}


def prompt_id(prompt: str = PROMPT, model: str = MODEL) -> str:
    return f"{model}:{hashlib.sha256(prompt.encode()).hexdigest()[:12]}"


def parse_json(text: str | None) -> dict | None:
    """Модель заворачивает JSON в ```json … ``` и кладёт СЫРЫЕ переводы строк внутрь строки
    `text` (замечено на первом же прогоне: 2 кадра из 16) — строгий парсер их не принимает,
    `strict=False` принимает. Вынимаем первый объект."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    for strict in (True, False):
        try:
            return json.loads(m.group(0), strict=strict)
        except json.JSONDecodeError:
            continue
    return None


def salvage_json(text: str) -> dict | None:
    """Ответ обрезан по max_tokens (плотный слайд с деревом файлов и кодом не влез в 1500
    токенов — ловилось). Обрыв почти всегда внутри самой длинной строки `text`: дозакрываем
    строку и объект, пробуя несколько хвостов."""
    i = text.find("{")
    if i < 0:
        return None
    body = text[i:].rstrip().rstrip("`").rstrip()
    for _ in range(12):
        # обрыв на обратном слэше ломает любой хвост — срезаем его
        body = body.rstrip("\\")
        for tail in ('", "visual": "", "unreadable": false}', '"}', '}', '"]}',
                     '"], "visual": "", "unreadable": false}'):
            try:
                return json.loads(body + tail, strict=False)
            except json.JSONDecodeError:
                continue
        # не сошлось — отступаем на строку назад и пробуем снова
        cut = max(body.rfind("\n"), body.rfind(","))
        if cut <= 0:
            break
        body = body[:cut].rstrip()
    return None


async def describe(client: httpx.AsyncClient, env: dict, image: Path, model: str, prompt: str,
                   sem: asyncio.Semaphore) -> tuple[dict | None, dict]:
    """Один вызов: (описание | None, метрики). Ошибка вызова — None с текстом в метриках."""
    img = base64.b64encode(image.read_bytes()).decode()
    body = {"model": model, "temperature": 0.1, "max_tokens": 3000, **NO_THINK,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}]}
    async with sem:
        t0 = time.time()
        j = None
        for attempt in range(3):
            try:
                r = await client.post(env["base_url"] + "/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {env['api_key']}"})
                r.raise_for_status()
                j = r.json()
                if not isinstance(j, dict):
                    raise ValueError(f"пустой ответ шлюза: {r.text[:80]!r}")  # 200 с телом `null` — повтор
                break
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError, OSError) as e:
                # OSError — и сырой ssl.SSLError (обрыв TLS, который httpx не заворачивает): повтор
                # обрыв соединения ловился при трёх параллельных запросах — повтор с паузой
                err = f"{type(e).__name__}: {str(e)[:160]}"
                await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:  # noqa: BLE001 — один сорванный кадр не роняет прогон
                return None, {"error": f"{type(e).__name__}: {str(e)[:160]}", "sec": round(time.time() - t0, 1)}
        if j is None:
            return None, {"error": err, "sec": round(time.time() - t0, 1)}
    choice = (j.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    usage = j.get("usage") or {}
    out = parse_json(content)
    meta = {"sec": round(time.time() - t0, 1), "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens"), "finish": choice.get("finish_reason")}
    if out is None and content and choice.get("finish_reason") == "length":
        out = salvage_json(content)
        if out is not None:
            meta["truncated"] = True
    if out is None:
        meta["error"] = "не JSON: " + (content or "<пусто>")[:120].replace("\n", " ")
        if not content:
            # пустой ответ — пишем сырой JSON рядом, причину видно только по нему
            dump = Path(os.environ.get("CLAUDE_JOB_DIR", "/tmp")) / "vision-empty.jsonl"
            with dump.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"image": image.name, "response": j}, ensure_ascii=False) + "\n")
    return out, meta


def as_text(v) -> str:
    """Модель иногда отдаёт `text` списком строк вместо строки — склеиваем, а не str()-им
    (ловилось: `['Pytest', 'Что это такое']` в индексе)."""
    if isinstance(v, list):
        return "\n".join(as_text(x) for x in v)
    if isinstance(v, dict):
        return "\n".join(f"{k}: {as_text(x)}" for k, x in v.items())
    return str(v or "")


def normalize_desc(d: dict) -> dict:
    kinds = {"slide", "code", "app", "terminal", "browser", "diagram", "people", "other"}
    kind = str(d.get("kind") or "other").strip().lower()
    return {"kind": kind if kind in kinds else "other",
            "title": as_text(d.get("title")).strip(),
            "text": as_text(d.get("text")).strip(),
            "visual": as_text(d.get("visual")).strip(),
            "unreadable": bool(d.get("unreadable"))}


def normalize_people(d: dict | None) -> dict:
    """Ответ на второй вопрос; незнакомое — `mixed`/`other`: сомнение трактуется как чужие лица."""
    d = d or {}
    who = str(d.get("who") or "").strip().lower()
    setting = str(d.get("setting") or "").strip().lower()
    try:
        count = int(d.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    return {"who": who if who in WHO else "mixed", "setting": setting if setting in SETTING else "other",
            "count": count}


def text_key(desc: dict) -> str:
    """Ключ тождества по тексту: заголовок + текст без пробелов и регистра."""
    raw = (desc.get("title", "") + "\n" + desc.get("text", "")).lower()
    return re.sub(r"\s+", "", raw)


def merge_by_text(slides: list[dict]) -> int:
    """Второй ярус тождества: одинаковый текст → один `slide`. Возвращает число склеек."""
    by_key: dict[str, int] = {}
    merged = 0
    for e in slides:
        d = e.get("desc")
        if not d or d["kind"] == "people" or d["unreadable"] or len(text_key(d)) < 20:
            continue
        k = text_key(d)
        if k in by_key and by_key[k] != e["slide"]:
            e["slide"] = by_key[k]
            merged += 1
        by_key.setdefault(k, e["slide"])
    return merged


async def run(record: Path, args) -> int:
    path = record / "record.slides.json"
    if not path.is_file():
        sys.exit(f"нет {path} — сначала slides_from_video.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    pid = prompt_id(PROMPT, args.model)
    items = []
    for group in ("slides", "samples"):
        if args.only and group != args.only:
            continue
        for e in data.get(group, []):
            if "frame" not in e:
                continue
            if group == "slides" and (e["t1"] - e["t0"]) < args.min_dur:
                continue
            if not args.redo and e.get("desc_meta", {}).get("prompt") == pid:
                continue
            # Люди: кадр удалён — описывать нечего; кадр ещё есть, а «кто это» не спрошено —
            # доспрашиваем (такие остались от прогонов до второго вопроса).
            if e.get("people") and ("who" in e or not e.get("frame")):
                continue
            items.append(e)
    # Заставка (первый видимый кадр, `intro_frame.py`) описывается наравне со слайдами: люди —
    # кадр удаляется, иначе desc идёт в выбор обложки.
    intro = data.get("intro")
    if intro and intro.get("frame") and args.only in (None, "intro") \
            and not (intro.get("people") and "who" in intro) \
            and (args.redo or intro.get("desc_meta", {}).get("prompt") != pid):
        items.append(intro)
    if args.limit:
        items = items[: args.limit]
    print(f"{record.name}: к описанию {len(items)} кадров (модель {args.model})")
    if args.dry or not items:
        return 0
    env = load_env()
    sem = asyncio.Semaphore(args.concurrency)
    stats = {"ok": 0, "err": 0, "people": 0, "speakers": 0, "sec": 0.0, "tokens_in": 0, "tokens_out": 0}
    t_all = time.time()

    async def one(client, e):
        img = record / e["frame"]
        out, meta = await describe(client, env, img, args.model, PROMPT, sem)
        stats["sec"] += meta.get("sec", 0)
        if out is None:
            stats["err"] += 1
            e["desc_error"] = meta.get("error")
            print(f"  ✗ {e['frame']}: {meta.get('error')}")
            return
        e.pop("desc_error", None)
        desc = normalize_desc(out)
        e["desc"] = desc
        e["desc_meta"] = {"model": args.model, "prompt": pid, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          **{k: v for k, v in meta.items() if k != "error"}}
        stats["tokens_in"] += meta.get("tokens_in") or 0
        stats["tokens_out"] += meta.get("tokens_out") or 0
        if desc["kind"] == "people":
            e["people"] = True
            e["desc"] = {"kind": "people", "title": "", "text": "", "visual": "", "unreadable": False}
            # второй вопрос: докладчик или чужие лица
            more, meta2 = await describe(client, env, img, args.model, PEOPLE_PROMPT, sem)
            stats["sec"] += meta2.get("sec", 0)
            ppl = normalize_people(more)
            e["who"], e["setting"] = ppl["who"], ppl["setting"]
            if ppl["who"] == "speaker":
                # докладчик — лицо доклада: кадр остаётся, обложкой станет по правилам make_cover
                stats["speakers"] += 1
                print(f"  ✓ {e.get('t_key', e.get('t'))}s: докладчик в кадре ({ppl['setting']}) — кадр остаётся")
            else:
                # ⚠️ чужие лица не храним: кадр — с диска, в сайдкаре только пометка
                stats["people"] += 1
                try:
                    img.unlink()
                except FileNotFoundError:
                    pass
                e.pop("frame", None)
                print(f"  · {e.get('t_key', e.get('t'))}s: люди ({ppl['who']}) — кадр удалён")
        else:
            stats["ok"] += 1
            print(f"  ✓ {e.get('t_key', e.get('t'))}s {desc['kind']:8s} {desc['title'][:50]!r} "
                  f"текст {len(desc['text'])} зн. {meta.get('sec')} с")

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
        # пишем сайдкар после каждой пачки, чтобы обрыв не терял сделанное
        for i in range(0, len(items), args.concurrency * 2):
            batch = items[i:i + args.concurrency * 2]
            await asyncio.gather(*(one(client, e) for e in batch))
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    merged = merge_by_text(data.get("slides", []))
    data["summary"]["unique_by_text"] = len({e["slide"] for e in data.get("slides", []) if e.get("desc")
                                             and e["desc"]["kind"] != "people"})
    data["summary"]["described"] = sum(1 for e in data.get("slides", []) if e.get("desc"))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    wall = time.time() - t_all
    print(f"описано {stats['ok']}, людей {stats['people']}, докладчиков {stats['speakers']}, ошибок {stats['err']}; "
          f"{wall:.0f} с стены, {stats['sec'] / max(len(items), 1):.1f} с на кадр; "
          f"токенов {stats['tokens_in']}+{stats['tokens_out']}; склеено по тексту {merged}")
    return 1 if stats["err"] and not stats["ok"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", type=Path, help="каталог записи с record.slides.json")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--only", choices=["slides", "samples", "intro"], help="описывать только слайды, только выборку или только заставку")
    ap.add_argument("--min-dur", type=float, default=0, help="слайды короче (с) не описывать")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--redo", action="store_true", help="описать заново, даже если отпечаток совпадает")
    ap.add_argument("--dry", action="store_true", help="только посчитать, без вызовов")
    a = ap.parse_args()
    return asyncio.run(run(a.record, a))


if __name__ == "__main__":
    sys.exit(main())
