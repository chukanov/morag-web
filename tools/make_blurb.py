#!/usr/bin/env python3
"""Краткое содержание записи для карточки — там, где у поста нет авторской аннотации.

Зачем (владелец, 13.09): у 96 записей из 190 в шапке нет `summary`, а две готовые сводки для
карточки не годятся: сводка ASR-конвейера написана в подкастных терминах («выпуск», «гости»),
сводка морага — для машины и по-английски. Здесь — 2-3 предложения по-русски «о чём и что
решили», по речи самой записи (начало расшифровки + заголовок и категория), текстовым вызовом
`Instruct` через тот же шлюз, что у остальных стадий. Результат — `blurb` в `record.meta.json`
с провенансом (модель, отпечаток промпта, время), а НЕ в шапку: шапка уезжает в payload каждого
чанка и стоила бы переиндексации, а сайт мету читать умеет. Где авторская аннотация есть —
ничего не делаем: она лучше.

  python3 tools/make_blurb.py --dry               # кому нужно
  python3 tools/make_blurb.py [--limit N] [--concurrency 2]
  python3 tools/make_blurb.py <каталог записи> --redo
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from describe_slides import load_env  # noqa: E402
from make_slides_md import read_header  # noqa: E402
from screen_refs import MODEL_TEXT, chat  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
VERSION = "blurb-v1"
HEAD_WORDS = 1800   # начала доклада хватает: тема, повод и план звучат в первые минуты

PROMPT = """Ниже — заголовок и начало расшифровки записи внутренней встречи или лекции компании.
Напиши краткое содержание для карточки записи на сайте: 2-3 предложения, 40-70 слов, по-русски.

Требования:
- О ЧЁМ запись по существу: какую систему, технологию или задачу разбирают и что именно про неё
  говорят (решение, подход, результат) — а не «докладчик рассказывает о…».
- Не называй имён и фамилий, не пиши «подкаст», «выпуск», «гости», «спикер».
- Названия систем и технологий — как они звучат в заголовке или расшифровке, без перевода.
- Не выдумывай того, чего нет в тексте; если начало не раскрывает существа, опиши тему по заголовку.
- Обычный текст: без разметки, списков, кавычек-обёрток и вступлений вроде «В этой записи».

Выведи ТОЛЬКО текст содержания.

Заголовок: {title}
Раздел: {branch}{category}

Начало расшифровки:
{text}
"""


def prompt_id(text: str) -> str:
    return f"{MODEL_TEXT}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def speech_head(md: str, words: int = HEAD_WORDS) -> str:
    """Начало речи без шапки, тайм-кодов и меток говорящих."""
    body = md.split("---", 2)[2] if md.startswith("---") else md
    body = re.sub(r"<!--.*?-->", "", body)
    body = re.sub(r"^\[[^\]]+\]\s*", "", body, flags=re.M)
    toks = body.split()
    return " ".join(toks[:words])


def candidates(root: Path, redo: bool = False) -> list[Path]:
    out = []
    for md in sorted(root.glob("**/record.md")):
        head = read_header(md)
        if head.get("summary", "").strip().strip('"'):
            continue                       # авторская аннотация есть — она лучше
        meta_path = md.parent / "record.meta.json"
        if not redo and meta_path.is_file():
            try:
                if (json.loads(meta_path.read_text(encoding="utf-8")).get("blurb") or {}).get("text"):
                    continue
            except ValueError:
                pass
        out.append(md.parent)
    return out


def build_prompt(rec: Path) -> str:
    md = (rec / "record.md").read_text(encoding="utf-8")
    head = read_header(rec / "record.md")
    cat = head.get("category", "").strip().strip('"')
    return PROMPT.format(title=head.get("title", "").strip().strip('"'), branch=head.get("branch", "").strip().strip('"'),
                         category=f" · {cat}" if cat else "", text=speech_head(md))


WRAPPER = re.compile(r"^(о чём (эта )?запись|краткое содержание( записи)?|содержание записи|запись о том)\s*[:—–-]\s*", re.I)


def clean(text: str) -> str:
    """Убираем обёртки, которые модель ставит вопреки промпту: «О чём запись:», «Краткое
    содержание записи:» — на первом прогоне 7 из 96 сводок начинались так."""
    text = re.sub(r"\s+", " ", text or "").strip().strip('"«»')
    text = WRAPPER.sub("", text).strip()
    return text[:1].upper() + text[1:] if text else text


async def one(client, env, rec: Path, sem, log) -> bool:
    prompt = build_prompt(rec)
    async with sem:
        content, meta = await chat(client, env, MODEL_TEXT, prompt, max_tokens=400, temperature=0.2)
    text = clean(content or "")
    if not text or meta.get("error"):
        log(f"  ✗ {rec.name[:52]:52s} {meta.get('error') or 'пусто'}")
        return False
    meta_path = rec / "record.meta.json"
    data = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    data["blurb"] = {"text": text, "version": VERSION, "model": MODEL_TEXT, "prompt": prompt_id(PROMPT),
                     "at": _dt.datetime.now().isoformat(timespec="seconds"), "tokens_in": meta.get("tokens_in"),
                     "tokens_out": meta.get("tokens_out")}
    meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"  ✓ {rec.name[:52]:52s} {len(text.split())} слов · {meta.get('sec')} с")
    return True


async def run(recs: list[Path], concurrency: int) -> int:
    import httpx  # noqa: PLC0415 — как у screen_refs
    env = load_env()
    sem = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(timeout=120) as client:
        results = await asyncio.gather(*(one(client, env, r, sem, print) for r in recs))
    return sum(1 for r in results if r)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", nargs="?", type=Path, help="одна запись; без неё — все, кому нужно")
    ap.add_argument("--root", type=Path, default=None, help="каталог записей (по умолчанию — каталог записей первого пространства корпуса)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--fix", action="store_true", help="перечистить уже записанные сводки (обёртки), без модели")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.root is None:
        import spaces  # noqa: PLC0415
        a.root = spaces.records_dirs()[0]
    if a.fix:
        fixed = 0
        for meta_path in sorted(a.root.glob("**/record.meta.json")):
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            text = (data.get("blurb") or {}).get("text")
            if text and clean(text) != text:
                data["blurb"]["text"] = clean(text)
                if not a.dry:
                    meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                fixed += 1
                print(f"  {meta_path.parent.name[:52]:52s} → {clean(text)[:70]}…")
        print(f"перечищено {fixed}{' (dry)' if a.dry else ''}")
        return 0
    recs = [a.record] if a.record else candidates(a.root, a.redo)
    if a.limit:
        recs = recs[: a.limit]
    print(f"записей без аннотации и без сводки: {len(recs)}")
    if a.dry:
        for r in recs:
            print("  ", r.name)
        return 0
    if not recs:
        return 0
    done = asyncio.run(run(recs, a.concurrency))
    print(f"сводок записано {done} из {len(recs)}")
    return 0 if done == len(recs) else 1


if __name__ == "__main__":
    sys.exit(main())
