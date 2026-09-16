#!/usr/bin/env python3
"""Категория, темы и формат записи — по таксономии корпуса, силами LLM.

    $MORAG_REPO/.venv/bin/python tools/classify.py --propose            # все записи → docs/taxonomy-proposal.md
    $MORAG_REPO/.venv/bin/python tools/classify.py --propose --limit 12 # обкатка на дюжине
    $MORAG_REPO/.venv/bin/python tools/classify.py --all                # записать classification в мету
    $MORAG_REPO/.venv/bin/python tools/classify.py --inbox              # артефакты в inbox/ (новая запись)

Что это. Записи корпуса не размечены по предмету: метки сайта одноразовые и наполовину фамилии,
`kind` из календаря — формат, а не предмет. Классификатор читает то, что о записи уже известно
БЕЗ полной расшифровки — заголовок, ветку, календарь и `doc_summary` с глоссарием из сайдкара
конвейера (они есть у всех записей), — и по таксономии `taxonomy.yml` семьи корпуса даёт одну
категорию, темы из словаря и формат там, где его нет.

⚠️ В шапку идут ТОЛЬКО значения из таксономии. Тема, которой в словаре нет, уезжает в
`new_topics` отчёта — решает владелец; иначе словарь расползётся, как метки сайта.
⚠️ Классификатор — у нас, не в движке: сайт читает шапки с диска, правка владельца живёт в мете
(`labels`), а вход лежит в наших сайдкарах. Движку достаточно увидеть поля в шапке.
⚠️ Интерпретатор — venv морага: нужен его LLM-клиент (`openai`); адрес и ключ шлюза — из
файл стека (`ASR_LLM_BASE_URL`, `ASR_LLM_MODEL`, `OR_KEY`), температура 0 и seed —
детерминизм, чтобы пересборка не переставляла категории сама по себе.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import spaces  # noqa: E402

TAXONOMY = spaces.family_dir() / "taxonomy.yml"
PROPOSAL_JSON = spaces.family_dir() / "classify" / "proposal.json"
PROPOSAL_DOC = REPO / "docs" / "taxonomy-proposal.md"
INBOX = spaces.family_dir() / "inbox"
GLOSSARY_CAP = 60       # терминов глоссария в промпт: дальше шум, а не сигнал
DOUBT = 0.65            # не выше — «спорная» запись в отчёте; вторая категория сама по себе не спор
CONCURRENCY = 4

SYSTEM = """Ты размечаешь записи внутренних встреч и учебных курсов IT-компании по заданной таксономии.
Отвечай строго JSON по схеме, без пояснений вне JSON.

Правила:
- category — РОВНО ОДНА из списка категорий: о чём запись ПО СУЩЕСТВУ (предмет), а не как рассказывали
  (формат). Выбирай по содержанию сводки, заголовок — подсказка. Если подходят две, главную — в category,
  вторую — в category_alt и снизь confidence.
- topics — темы ТОЛЬКО из словаря тем, в точности как написаны там: внутренние системы, предметная
  область, технологии, которые ЗАМЕТНО обсуждались (не всё, что мимоходом упомянуто). 2–8 штук.
- new_topics — важная тема, которой в словаре нет (система, технология, предмет). Коротко, 1–3 слова.
  Не выдумывай: только то, что явно есть в сводке или терминах.
- kind — формат записи из списка форматов, ТОЛЬКО если в записи он пуст; иначе null.
- confidence — 0..1, насколько уверен в category.
- why — одна фраза, почему такая категория.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "category_alt": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "new_topics": {"type": "array", "items": {"type": "string"}},
        "kind": {"type": ["string", "null"]},
        "why": {"type": "string"},
    },
    "required": ["category", "category_alt", "confidence", "topics", "new_topics", "kind", "why"],
    "additionalProperties": False,
}


# --- таксономия -----------------------------------------------------------


def load_taxonomy(path: Path = TAXONOMY) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _key(text: str) -> str:
    return re.sub(r"[\s\-_./]+", "", str(text).replace("ё", "е").replace("Ё", "Е")).lower()


def canon_index(tax: dict) -> dict[str, tuple[str, str]]:
    """Ключ написания → (каноническая тема, группа). Канон тоже ключ — он алиас самому себе."""
    out: dict[str, tuple[str, str]] = {}
    for group, items in (tax.get("topics") or {}).items():
        for item in items or []:
            name = str(item["name"])
            out.setdefault(_key(name), (name, group))
            for alias in item.get("aliases") or []:
                out.setdefault(_key(alias), (name, group))
    return out


def canonize(values: list[str], index: dict[str, tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Ответ модели → (темы из словаря, незнакомые). Порядок и уникальность сохраняются."""
    known: list[str] = []
    unknown: list[str] = []
    for value in values or []:
        value = str(value).strip()
        if not value:
            continue
        hit = index.get(_key(value))
        if hit:
            if hit[0] not in known:
                known.append(hit[0])
        elif value not in unknown:
            unknown.append(value)
    return known, unknown


def category_names(tax: dict) -> list[str]:
    return [str(c["name"]) for c in tax.get("categories") or []]


def match_category(value: str, categories: list[str]) -> str:
    """Имя категории из ответа модели → имя из таксономии. Точно, иначе с опечаткой в одну-две
    буквы (замерено: «Комьютерное зрение» вместо «Компьютерное»); дальше — не категория."""
    value = str(value or "").strip()
    if value in categories:
        return value
    key = _key(value)
    best = difflib.get_close_matches(key, [_key(c) for c in categories], n=1, cutoff=0.92)
    if best:
        return categories[[_key(c) for c in categories].index(best[0])]
    return ""


def taxonomy_block(tax: dict) -> str:
    lines = ["КАТЕГОРИИ (выбрать одну):"]
    for c in tax.get("categories") or []:
        hints = ", ".join(c.get("hints") or [])
        lines.append(f"- {c['name']} — {c.get('about', '')}" + (f" Подсказки: {hints}." if hints else ""))
    lines.append("")
    lines.append("СЛОВАРЬ ТЕМ (писать в точности так):")
    for group, items in (tax.get("topics") or {}).items():
        lines.append(f"- {group}: " + "; ".join(str(i["name"]) for i in items or []))
    lines.append("")
    lines.append("ФОРМАТЫ (kind): " + "; ".join(str(k) for k in tax.get("kinds") or []))
    return "\n".join(lines)


# --- вход по записи -------------------------------------------------------


def parse_head(text: str) -> dict:
    head = text.split("---", 2)
    if len(head) < 3:
        return {}
    out = {}
    for line in head[1].splitlines():
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if not key.strip() or not raw:
            continue
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def sidecar_bits(path: Path) -> tuple[str, list[str]]:
    """`doc_summary` и канонические термины глоссария из сайдкара (или артефакта в inbox)."""
    if not path.is_file():
        return "", []
    try:
        x = json.loads(path.read_text(encoding="utf-8")).get("x_enriched") or {}
    except (ValueError, OSError):
        return "", []
    terms: list[str] = []
    for entry in x.get("glossary") or []:
        for canonical in entry.get("canonicals") or []:
            canonical = str(canonical).strip()
            if canonical and canonical not in terms:
                terms.append(canonical)
    return str(x.get("doc_summary") or "").strip(), terms


def record_input(record_dir: Path) -> dict:
    head = parse_head((record_dir / "record.md").read_text(encoding="utf-8"))
    meta = {}
    meta_path = record_dir / "record.meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    doc_summary, terms = sidecar_bits(record_dir / "record.json")
    parts = record_dir.resolve().parts
    branch = sub = ""
    if "records" in parts:
        tail = parts[parts.index("records") + 1:]
        branch, sub = (tail[0] if len(tail) > 2 else ""), (tail[1] if len(tail) > 2 else "")
    return {
        "id": record_dir.name, "title": str(head.get("title") or ""), "date": str(head.get("date") or ""),
        "branch": branch, "sub": sub, "event": str(head.get("event") or ""),
        "kind": list(head.get("kind") or []), "tags": list(head.get("tags") or []),
        "summary": str(head.get("summary") or "")[:600],
        "calendar_topic": str((meta.get("talk") or {}).get("topic") or ""),
        "speakers": list(head.get("speakers") or []),
        "doc_summary": doc_summary, "terms": terms,
    }


def inbox_input(artifact: Path) -> dict:
    """Новая запись до сборки: артефакт `inbox/<id>.json` и мета рядом с ним."""
    meta = {}
    meta_path = artifact.with_suffix(".meta.json")
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    doc_summary, terms = sidecar_bits(artifact)
    talk = meta.get("talk") or {}
    return {
        "id": artifact.stem, "title": str(meta.get("title") or artifact.stem), "date": str(talk.get("date") or ""),
        "branch": "", "sub": "", "event": "", "kind": list(talk.get("kind") or []), "tags": [],
        "summary": str((meta.get("summary") or {}).get("text") or "")[:600],
        "calendar_topic": str(talk.get("topic") or ""),
        "speakers": [s["name"] for s in meta.get("speakers") or [] if s.get("name")],
        "doc_summary": doc_summary, "terms": terms,
    }


def prompt_for(inp: dict, tax: dict, index: dict) -> str:
    # Термины: сперва те, что есть в словаре (по ним темы), потом остальные, всего не больше CAP.
    known = [t for t in inp["terms"] if _key(t) in index]
    other = [t for t in inp["terms"] if _key(t) not in index]
    terms = (known + other)[:GLOSSARY_CAP]
    lines = [
        taxonomy_block(tax), "",
        "ЗАПИСЬ:",
        f"Заголовок: {inp['title']}",
        f"Ветка: {inp['branch'] or '—'}" + (f" / {inp['sub']}" if inp['sub'] else "") + (f" · {inp['event']}" if inp['event'] else ""),
        f"Дата: {inp['date'] or '—'}",
        f"Формат (kind) в записи: {', '.join(inp['kind']) or 'пусто'}",
        f"Тема по календарю: {inp['calendar_topic'] or '—'}",
        f"Метки поста: {', '.join(inp['tags']) or '—'}",
        f"Выступали: {', '.join(inp['speakers']) or '—'}",
        f"Аннотация поста: {inp['summary'] or '—'}",
        f"Сводка расшифровки: {inp['doc_summary'] or '—'}",
        f"Термины из глоссария: {', '.join(terms) or '—'}",
    ]
    return "\n".join(lines)


# --- LLM ------------------------------------------------------------------


def load_env() -> dict:
    """Адрес, модель и ключ шлюза из файла стека транскрибации (`describe_slides.stack_env_file`:
    `$ASR_STACK_ENV` → `ops.env` корпуса → `~/.asr-stack.env`). Прокси из оболочки снимаем."""
    from describe_slides import stack_env_file  # noqa: PLC0415
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(var, None)
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
    model = os.environ.get("ASR_LLM_MODEL") or values.get("ASR_LLM_MODEL") or ""
    key = os.environ.get("OR_KEY") or values.get("OR_KEY") or ""
    if not (base and model and key):
        sys.exit(f"нет адреса/модели/ключа LLM: ASR_LLM_BASE_URL, ASR_LLM_MODEL, OR_KEY в {env_file}")
    return {"base_url": base, "model": model, "api_key": key,
            "repo": os.environ.get("MORAG_REPO") or values.get("MORAG_REPO") or ""}


def build_llm(env: dict):
    # Чекаут движка: `MORAG_REPO`, иначе сосед `../morag` (как у стека транскрибации).
    repo = env["repo"] or str(Path(__file__).resolve().parents[2] / "morag")
    sys.path.insert(0, str(Path(repo).expanduser() / "src"))
    try:
        from morag.llm.client import LLMClient
    except ImportError as e:
        sys.exit(f"нет LLM-клиента морага ({e}) — запускайте $MORAG_REPO/.venv/bin/python")
    return LLMClient(base_url=env["base_url"], model=env["model"], api_key=env["api_key"],
                     enable_thinking=False, timeout=180, max_retries=3, max_concurrent=CONCURRENCY)


async def classify_one(llm, inp: dict, tax: dict, index: dict, categories: list[str]) -> dict:
    user = prompt_for(inp, tax, index)
    try:
        res = await llm.complete_json(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            schema=SCHEMA, schema_name="classify", max_tokens=800)
    except Exception as e:  # noqa: BLE001 — один сорванный вызов не роняет прогон
        return {"id": inp["id"], "error": f"{type(e).__name__}: {str(e)[:120]}"}
    res = res or {}
    topics, unknown = canonize(res.get("topics") or [], index)
    category = str(res.get("category") or "").strip()
    alt = match_category(res.get("category_alt") or "", categories)
    kind = res.get("kind")
    out = {
        "id": inp["id"], "title": inp["title"], "branch": inp["branch"], "sub": inp["sub"], "date": inp["date"],
        "category": match_category(category, categories),
        "category_raw": category, "category_alt": alt or None,
        "confidence": float(res.get("confidence") or 0),
        "topics": topics, "new_topics": [str(x) for x in (res.get("new_topics") or [])] + unknown,
        "kind": kind if (kind in (tax.get("kinds") or []) and not inp["kind"]) else None,
        "had_kind": bool(inp["kind"]),
        "why": str(res.get("why") or ""),
    }
    return out


async def classify_many(inputs: list[dict], tax: dict) -> list[dict]:
    env = load_env()
    llm = build_llm(env)
    index = canon_index(tax)
    categories = category_names(tax)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async def one(inp):
        nonlocal done
        async with sem:
            res = await classify_one(llm, inp, tax, index, categories)
        done += 1
        mark = "⚠️" if res.get("error") or not res.get("category") else " "
        print(f"{mark} {done:3d}/{len(inputs)} {inp['id'][:52]:52} → {res.get('category') or res.get('error', '?')}")
        return res

    results = await asyncio.gather(*(one(i) for i in inputs))
    return results


def fingerprint(env: dict, tax: dict) -> dict:
    prompt_sha = hashlib.sha256((SYSTEM + taxonomy_block(tax)).encode("utf-8")).hexdigest()[:12]
    return {"model": env["model"], "prompt_sha": prompt_sha, "when": datetime.now().isoformat(timespec="minutes")}


# --- отчёт для владельца ---------------------------------------------------


def write_proposal(results: list[dict], tax: dict, stamp: dict) -> None:
    cats = category_names(tax)
    by_cat: dict[str, list[dict]] = {c: [] for c in cats}
    orphans = [r for r in results if not r.get("category")]
    for r in results:
        if r.get("category"):
            by_cat[r["category"]].append(r)
    topic_freq = Counter(t for r in results for t in r.get("topics") or [])
    new_freq = Counter(t.strip().lower() for r in results for t in r.get("new_topics") or [])
    doubtful = [r for r in results if r.get("category") and r["confidence"] <= DOUBT]
    kind_fill = [r for r in results if r.get("kind")]

    L: list[str] = []
    L.append("# Таксономия корпуса: предложение\n")
    L.append(f"Разметка {len(results)} записей классификатором (`tools/classify.py --propose`, модель "
             f"`{stamp['model']}`, {stamp['when']}, промпт `{stamp['prompt_sha']}`) по черновой таксономии "
             "`taxonomy.yml` корпуса. Файл генерируется; правки — в таксономию (имена, определения, "
             "слияния) и в `labels` меты конкретной записи, потом прогон заново.\n")
    L.append("⚠️ Имена коллег и внутренние названия: документ живёт только в этом репозитории.\n")
    L.append("## Категории\n")
    L.append("| категория | записей | о чём |\n|---|---:|---|")
    for c in tax.get("categories") or []:
        L.append(f"| {c['name']} | {len(by_cat[c['name']])} | {c.get('about', '')} |")
    if orphans:
        L.append(f"| *(без категории — ответ вне списка или сбой)* | {len(orphans)} | |")
    L.append("")
    L.append(f"## Спорные ({len(doubtful)}): уверенность не выше {DOUBT}\n")
    L.append("Вторая категория (`category_alt`) стоит у большинства записей и сама по себе спором не "
             "считается — она видна в таблицах раскладки.\n")
    L.append("| запись | категория | вторая | увер. | почему |\n|---|---|---|---:|---|")
    for r in sorted(doubtful, key=lambda r: r["confidence"]):
        L.append(f"| {r['date']} {r['title'][:60]} | {r['category']} | {r.get('category_alt') or ''} | "
                 f"{r['confidence']:.2f} | {r['why'][:120]} |")
    L.append("")
    L.append("## Раскладка по категориям\n")
    for c in cats:
        rows = sorted(by_cat[c], key=lambda r: (r["branch"], r["date"]))
        L.append(f"### {c} — {len(rows)}\n")
        if not rows:
            L.append("*пусто*\n")
            continue
        L.append("| ветка | дата | запись | увер. | темы |\n|---|---|---|---:|---|")
        for r in rows:
            L.append(f"| {r['branch']}{(' / ' + r['sub']) if r['sub'] and not r['sub'].isdigit() else ''} | "
                     f"{r['date']} | {r['title'][:70]} | {r['confidence']:.2f} | {', '.join(r['topics'])} |")
        L.append("")
    if orphans:
        L.append("### Без категории\n")
        for r in orphans:
            L.append(f"- {r['id']}: {r.get('error') or ('ответ «' + r.get('category_raw', '') + '» не из списка')}")
        L.append("")
    L.append("## Темы — сколько записей\n")
    L.append("| тема | записей |\n|---|---:|")
    for t, n in topic_freq.most_common():
        L.append(f"| {t} | {n} |")
    L.append("")
    L.append(f"## Предложенные новые темы (нет в словаре) — {len(new_freq)}\n")
    L.append("Добавить в `taxonomy.yml` те, что нужны фильтру; остальное — шум.\n")
    L.append("| тема | записей |\n|---|---:|")
    for t, n in new_freq.most_common():
        if n >= 1:
            L.append(f"| {t} | {n} |")
    L.append("")
    L.append(f"## Формат там, где его не было — {len(kind_fill)}\n")
    L.append("| запись | предложен kind |\n|---|---|")
    for r in sorted(kind_fill, key=lambda r: (r["branch"], r["date"])):
        L.append(f"| {r['branch']} · {r['date']} · {r['title'][:60]} | {r['kind']} |")
    L.append("")
    PROPOSAL_DOC.write_text("\n".join(L) + "\n", encoding="utf-8")


# --- запись в мету ---------------------------------------------------------


def write_meta(path: Path, result: dict, stamp: dict, dry: bool) -> bool:
    """`classification` в `record.meta.json`. `labels` (рука владельца) не трогаем."""
    try:
        meta = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        meta = {}
    block = {
        "category": result.get("category") or "",
        "category_alt": result.get("category_alt"),
        "topics": result.get("topics") or [],
        "kind": result.get("kind"),
        "confidence": round(float(result.get("confidence") or 0), 2),
        "why": result.get("why") or "",
        "new_topics": result.get("new_topics") or [],
        "from": "llm", **stamp,
    }
    if meta.get("classification") == block:
        return False
    meta["classification"] = block
    if not dry:
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


# --- CLI ------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", nargs="*", help="id записей (по умолчанию все)")
    ap.add_argument("--propose", action="store_true", help="отчёт для владельца, мету не трогать")
    ap.add_argument("--all", action="store_true", help="записать classification в record.meta.json")
    ap.add_argument("--inbox", action="store_true", help="артефакты в inbox/ — до сборки записи")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="переразметить и те, у кого отпечаток свежий")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if not (args.propose or args.all or args.inbox):
        ap.print_help()
        return 2

    tax = load_taxonomy()
    if args.inbox:
        artifacts = [p for p in sorted(INBOX.glob("*.json")) if not p.name.endswith((".meta.json", ".hints.json"))]
        if args.record:
            artifacts = [a for a in artifacts if a.stem in set(args.record)]
        inputs = [inbox_input(a) for a in artifacts]
        targets = {a.stem: a.with_suffix(".meta.json") for a in artifacts}
    else:
        dirs = spaces.record_dirs("record.md")
        if args.record:
            want = set(args.record)
            dirs = [d for d in dirs if d.name in want]
        if args.limit:
            dirs = dirs[: args.limit]
        inputs = [record_input(d) for d in dirs]
        targets = {d.name: d / "record.meta.json" for d in dirs}
    if not inputs:
        print("нечего классифицировать")
        return 0

    stamp = fingerprint(load_env(), tax)
    # Переразметка — только устаревших: у кого в мете тот же промпт и та же модель, тот размечен
    # этой же таксономией, и звать LLM заново незачем (смена таксономии меняет prompt_sha).
    # Свежий кэш `--propose` с тем же отпечатком тоже годится вместо вызова.
    cached: dict[str, dict] = {}
    if PROPOSAL_JSON.is_file():
        prev = json.loads(PROPOSAL_JSON.read_text(encoding="utf-8"))
        if (prev.get("stamp") or {}).get("prompt_sha") == stamp["prompt_sha"] and (prev.get("stamp") or {}).get("model") == stamp["model"]:
            cached = {r["id"]: r for r in prev.get("results") or [] if r.get("category")}
    todo, ready = [], []
    for inp in inputs:
        current = {}
        if not args.inbox and not args.propose:
            try:
                current = (json.loads(targets[inp["id"]].read_text(encoding="utf-8")).get("classification") or {}) \
                    if targets[inp["id"]].is_file() else {}
            except ValueError:
                current = {}
        if not args.force and current.get("prompt_sha") == stamp["prompt_sha"] and current.get("model") == stamp["model"]:
            continue  # размечено этой же таксономией
        if not args.force and inp["id"] in cached:
            ready.append(cached[inp["id"]])
        else:
            todo.append(inp)
    print(f"записей {len(inputs)}: из кэша {len(ready)}, к LLM {len(todo)}, уже размечены {len(inputs) - len(ready) - len(todo)}")
    results = ready + (asyncio.run(classify_many(todo, tax)) if todo else [])
    errors = [r for r in results if r.get("error")]
    if args.propose:
        PROPOSAL_JSON.parent.mkdir(parents=True, exist_ok=True)
        # Прогон по части записей ДОПОЛНЯЕТ кэш, а не затирает: иначе точечная переразметка
        # одной записи оставила бы в отчёте её одну.
        if (args.record or args.limit or ready) and PROPOSAL_JSON.is_file():
            previous = json.loads(PROPOSAL_JSON.read_text(encoding="utf-8")).get("results") or []
            fresh = {r["id"] for r in results}
            results = [r for r in previous if r["id"] not in fresh] + results
        PROPOSAL_JSON.write_text(json.dumps({"stamp": stamp, "results": results}, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        write_proposal(results, tax, stamp)
        print(f"\nотчёт: {PROPOSAL_DOC.relative_to(REPO)} · кэш: {PROPOSAL_JSON.relative_to(REPO)}"
              f" · сбоев {len(errors)}")
    else:
        written = 0
        for r in results:
            if r.get("error"):
                continue
            written += write_meta(targets[r["id"]], r, stamp, args.dry)
        print(f"\nмета обновлена у {written} из {len(results)}" + (" (--dry)" if args.dry else "")
              + f" · сбоев {len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
