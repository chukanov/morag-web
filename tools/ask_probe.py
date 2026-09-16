#!/usr/bin/env python3
"""Задать вопрос движку пространства и увидеть ВСЁ: ходы агента, ответ, цитаты.

Зачем отдельный инструмент, когда есть сайт: настройка промпта — это десятки прогонов подряд, и
смотреть надо не на текст ответа, а на то, ЧЕМ агент его добыл. Сайт показывает результат, здесь
видно ленту: в какой инструмент пошёл, с каким запросом, что вернулось. Правка конфига движка
подхватывается по mtime — рестарт между прогонами не нужен.

    python3 tools/ask_probe.py "Что рассказывали про архивацию через Flink?"
    python3 tools/ask_probe.py --slug demo --quiet "кто выступал в 2024 году"

Адрес и модель берутся из app/config.yml (карта `engines`), как их видит сам сайт, — чтобы
инструмент не разъехался с приложением.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULTS = {"base_url": "http://127.0.0.1:9099", "model": "morag", "api_key": "0p3n-w3bu!"}


def engine_of(slug: str) -> dict:
    """Движок пространства: `engines[slug]`, иначе общий `engine`, иначе дефолты.

    ⚠️ Повторяет `app.config.engine_for`, включая то, что мержа поверх общего `engine` НЕТ:
    незаданные поля берутся из дефолтов, а не подмешиваются. Разойдётся с приложением —
    инструмент будет спрашивать не тот движок и врать об этом молча.
    """
    import yaml

    # Тот же слой конфигов, что у сайта: пример ← рабочий (`MORAG_WEB_CONFIG` или app/config.yml).
    cfg = {}
    working = os.environ.get("MORAG_WEB_CONFIG") or str(REPO / "app" / "config.yml")
    for path in (REPO / "app" / "config.example.yml", pathlib.Path(working)):
        if path.is_file():
            cfg.update(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    engines = cfg.get("engines") or {}
    engine = engines.get(slug) or engines.get(slug.replace("-", "_")) or cfg.get("engine") or {}
    return {**DEFAULTS, **{k: v for k, v in engine.items() if k in DEFAULTS}}


def literal_placeholder(answer: str) -> int:
    """Сколько раз агент написал «[N]» БУКВОЙ вместо номера.

    Отдельный способ сломаться, замеченный на живом ответе: инструкция «якори номером [N]»
    читается моделью как «поставь сюда [N]», и ответ выглядит осмысленным, но ни одна ссылка не
    кликается. Числовые ссылки при этом могут быть рядом, поэтому проверка отдельная.
    """
    return len(re.findall(r"\[N\]", answer or ""))


def anchors_missing(answer: str, citations: list) -> bool:
    """Цитаты есть, а ссылок [N] в тексте нет — ответ, из которого некуда перейти.

    Отдельная проверка, потому что ломается это МОЛЧА и снаружи выглядит нормально: внизу
    источники на месте, ответ читается, и только клика нет. Ловилось 09.09 — правка промпта
    про безымянные голоса содержала образец «ВЕРНО» без единого [N], и модель приняла его за
    эталон ответа целиком; маркеры исчезли из ВСЕХ ответов разом.
    """
    return bool(citations) and not re.search(r"\[\d+\]", answer or "")


def orphan_anchors(answer: str, citations: list) -> list[int]:
    """Номера, которые агент поставил в текст, а карточки под ними нет.

    ⚠️ `anchors_missing` этого не ловит: там проверка «всё или ничего», а здесь ответ выглядит
    полностью исправным — часть ссылок кликается, часть нет. На фронте лишний номер остаётся
    простым текстом (`web/js/chat/md.js` подставляет ссылку только для существующей карточки),
    то есть выглядит цитатой и никуда не ведёт.
    Замерено 10.09: 27 и 9 таких номеров за прогон, все — на ответах по каталогу, где чанков нет
    вовсе, а перечень строк модель всё равно размечает сносками.
    """
    have = {c["n"] for c in citations if c.get("n") is not None}
    used = {int(x) for x in re.findall(r"\[(\d+)\]", answer or "")}
    return sorted(used - have)


def unresolved_citations(citations: list, slug: str) -> list[str]:
    """Цитаты, чей doc_id не разворачивается в запись корпуса.

    ⚠️⚠️ Это единственная проверка, которая ловит поломку САМОГО ЦЕННОГО в ответе — перехода из
    цитаты к секунде видео. Остальные метрики (число цитат, якоря, протечки) остаются зелёными,
    даже если ни одна карточка больше не разрешается: ответ читается, источники перечислены,
    и только кликнуть некуда. Разворот у нас делает `app/content/resolve.py` по ПУТИ внутри
    doc_id, поэтому здесь проверяется ровно он — существует ли такой файл в корпусе.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import spaces  # noqa: PLC0415
    records = spaces.records_dir(slug)
    bad = []
    for c in citations:
        doc = (c.get("doc") or "").split("#", 1)[0]
        parts = doc.split(":", 2)
        rel = parts[2] if len(parts) == 3 else doc
        if not rel or not (records / rel).exists():
            bad.append(doc or "<пусто>")
    return bad


def ask(question: str, engine: dict, quiet: bool) -> tuple[str, list[dict], list[dict]]:
    """Стрим OpenAI-совместимого ответа: (текст, цитаты, лента статусов)."""
    payload = {
        "model": engine["model"],
        "stream": True,
        "messages": [{"role": "user", "content": question}],
    }
    request = urllib.request.Request(
        engine["base_url"].rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {engine['api_key']}",
            "Content-Type": "application/json",
        },
    )
    answer: list[str] = []
    citations: list[dict] = []
    statuses: list[dict] = []
    started = time.monotonic()
    # ⚠️ Без прокси: движок локальный, а ambient HTTPS_PROXY завернул бы петлю наружу.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            body = line[6:]
            if body == "[DONE]":
                break
            try:
                event = json.loads(body)
            except json.JSONDecodeError:
                continue
            kind = (event.get("event") or {}).get("type")
            if kind == "status":
                data = event["event"].get("data") or {}
                statuses.append({"t": round(time.monotonic() - started, 1), "text": data.get("description", "")})
                if not quiet:
                    print(f"  [{statuses[-1]['t']:>5.1f}с] {statuses[-1]['text']}", file=sys.stderr)
                continue
            if kind == "citation":
                data = event["event"].get("data") or {}
                for meta in data.get("metadata") or []:
                    citations.append({
                        "n": meta.get("citation_number"),
                        "doc": meta.get("source", ""),
                        "name": (data.get("source") or {}).get("name", ""),
                        "found_by": meta.get("found_by"),
                    })
                continue
            for choice in event.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    answer.append(piece)
                    if not quiet:
                        sys.stdout.write(piece)
                        sys.stdout.flush()
    return "".join(answer), citations, statuses


def run_probes(args) -> int:
    """Прогнать весь набор и сложить отчёт одним файлом.

    Смысл не в оценке (её ставит человек), а в том, чтобы после правки промпта СРАВНИТЬ два
    прогона целиком, а не помнить, как оно отвечало вчера. Поэтому в отчёт идут не только ответы,
    но и ходы агента: половина правок промпта меняет именно их.
    """
    import datetime
    import yaml

    engine = engine_of(args.slug)
    if args.base_url:
        engine["base_url"] = args.base_url
    if args.model:
        engine["model"] = args.model

    probes_path = pathlib.Path(args.probes)
    probes = (yaml.safe_load(probes_path.read_text(encoding="utf-8")) or {}).get("probes") or []
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    out = pathlib.Path(args.out) if args.out else probes_path.with_name(f"probe-run-{stamp}.md")

    report = [f"# Прогон набора {probes_path.name} — {stamp}", "",
              f"Движок: `{engine['base_url']}` / `{engine['model']}`", ""]
    leaks = 0
    anchorless = 0
    literals = 0
    orphaned = 0
    unresolvable = 0
    for i, probe in enumerate(probes, 1):
        question = probe["вопрос"]
        print(f"[{i}/{len(probes)}] {question}", file=sys.stderr)
        started = time.monotonic()
        try:
            answer, citations, statuses = ask(question, engine, quiet=True)
        except OSError as exc:
            answer, citations, statuses = f"<движок не ответил: {exc}>", [], []
        took = time.monotonic() - started
        leaked = sorted({w.strip("«».,:;()[]") for w in answer.split()
                         if w.strip("«».,:;()[]").startswith("Speaker_")})
        leaks += bool(leaked)
        noanchor = anchors_missing(answer, citations)
        anchorless += noanchor
        literal = literal_placeholder(answer)
        literals += bool(literal)
        orphans = orphan_anchors(answer, citations)
        orphaned += bool(orphans)
        unresolved = unresolved_citations(citations, args.slug)
        unresolvable += bool(unresolved)
        print(f"      {took:.0f} с, ходов {len(statuses)}, цитат {len(citations)}"
              + (f"  ⚠️ ПРОТЁК {', '.join(leaked)}" if leaked else "")
              + ("  ⚠️ БЕЗ ССЫЛОК [N]" if noanchor else "")
              + (f"  ⚠️ «[N]» БУКВОЙ ×{literal}" if literal else "")
              + (f"  ⚠️ СИРОТЫ {orphans}" if orphans else "")
              + (f"  ⚠️ НЕ РАЗРЕШИЛИСЬ ×{len(unresolved)}" if unresolved else ""), file=sys.stderr)
        report += [
            f"## {i}. {question}", "",
            f"*класс: {probe.get('класс', '—')} · ждём: {probe.get('ждём', '—')}*", "",
            f"`{took:.0f} с · ходов {len(statuses)} · цитат {len(citations)}`"
            + ("  **⚠️ ПРОТЁК ИДЕНТИФИКАТОР ГОЛОСА: " + ", ".join(leaked) + "**" if leaked else "")
            + ("  **⚠️ НИ ОДНОЙ ССЫЛКИ [N] В ТЕКСТЕ**" if noanchor else "")
            + (f"  **⚠️ «[N]» НАПИСАНО БУКВОЙ ×{literal}**" if literal else "")
            + (f"  **⚠️ НОМЕРА БЕЗ КАРТОЧКИ: {orphans}**" if orphans else "")
            + ("  **⚠️ ЦИТАТЫ НЕ РАЗРЕШИЛИСЬ В ЗАПИСЬ: " + ", ".join(unresolved) + "**"
               if unresolved else ""), "",
            "<details><summary>ходы агента</summary>", "",
            *[f"- `{s['t']:>5.1f}с` {s['text']}" for s in statuses], "",
            "</details>", "", answer, "",
            "**Цитаты:** " + (", ".join(f"[{c['n']}] {c['name']}" for c in citations) or "нет"), "", "---", "",
        ]
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\nотчёт: {out}"
          + (f"\n⚠️ протечек Speaker_N: {leaks} из {len(probes)}" if leaks else "")
          + (f"\n⚠️ ответов без ссылок [N]: {anchorless} из {len(probes)}" if anchorless else "")
          + (f"\n⚠️ ответов с буквальным «[N]»: {literals} из {len(probes)}" if literals else "")
          + (f"\n⚠️ ответов с номерами без карточки: {orphaned} из {len(probes)}" if orphaned else "")
          + (f"\n⚠️ ответов с неразрешимыми цитатами: {unresolvable} из {len(probes)}"
             if unresolvable else ""),
          file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", help="вопрос (или --probes для набора)")
    parser.add_argument("--slug", default=None, help="пространство (по умолчанию — первое в корпусе)")
    parser.add_argument("--quiet", action="store_true", help="не печатать ленту и поток, только итог")
    # Ручной адрес — чтобы сравнить два конфига бок о бок: поднять кандидата на соседнем порту
    # и спросить одно и то же у обоих, не трогая рабочий движок.
    parser.add_argument("--base-url", help="адрес движка вместо взятого из app/config.yml")
    parser.add_argument("--model", help="имя модели (PIPELINE_NAME) вместо взятого из конфига")
    parser.add_argument("--probes", help="файл с набором контрольных вопросов — прогнать все подряд")
    parser.add_argument("--out", help="куда сложить отчёт прогона (по умолчанию рядом с набором)")
    args = parser.parse_args()
    if args.slug is None:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import spaces  # noqa: PLC0415
        args.slug = spaces.slugs()[0]
    if not args.question and not args.probes:
        parser.error("нужен вопрос или --probes")

    if args.probes:
        return run_probes(args)

    engine = engine_of(args.slug)
    if args.base_url:
        engine["base_url"] = args.base_url
    if args.model:
        engine["model"] = args.model
    print(f"=== {args.slug}: {engine['base_url']} / {engine['model']}", file=sys.stderr)
    print(f"=== ВОПРОС: {args.question}\n", file=sys.stderr)

    started = time.monotonic()
    try:
        answer, citations, statuses = ask(args.question, engine, args.quiet)
    except OSError as exc:
        print(f"движок не ответил: {exc}", file=sys.stderr)
        return 1
    took = time.monotonic() - started

    if args.quiet:
        print(answer)
    tools = [s["text"] for s in statuses]
    print(f"\n\n=== {took:.1f} с · ходов {len(tools)} · цитат {len(citations)}", file=sys.stderr)
    # Speaker_N в ответе — прямое нарушение правил промпта, поэтому проверяем каждый прогон.
    leaked = sorted({w for w in answer.split() if w.strip('«».,:;()[]').startswith("Speaker_")})
    if leaked:
        print(f"⚠️ В ОТВЕТЕ ПРОТЁК ИДЕНТИФИКАТОР ГОЛОСА: {', '.join(leaked)}", file=sys.stderr)
    if anchors_missing(answer, citations):
        print("⚠️ В ТЕКСТЕ НЕТ НИ ОДНОЙ ССЫЛКИ [N], хотя источники найдены — перейти некуда",
              file=sys.stderr)
    orphans = orphan_anchors(answer, citations)
    if orphans:
        print(f"⚠️ НОМЕРА БЕЗ КАРТОЧКИ: {orphans} — выглядят ссылкой и никуда не ведут",
              file=sys.stderr)
    unresolved = unresolved_citations(citations, args.slug)
    if unresolved:
        print(f"⚠️ ЦИТАТЫ НЕ РАЗВЕРНУЛИСЬ В ЗАПИСЬ ({len(unresolved)}): " + ", ".join(unresolved[:3]),
              file=sys.stderr)
    if literal_placeholder(answer):
        print(f"⚠️ «[N]» НАПИСАНО БУКВОЙ {literal_placeholder(answer)} раз — такие ссылки не кликаются",
              file=sys.stderr)
    if citations:
        print("=== ЦИТАТЫ", file=sys.stderr)
        for c in citations:
            by = (c.get("found_by") or [{}])[0] if isinstance(c.get("found_by"), list) else {}
            hint = f"  ← {by.get('tool')}({by.get('query')!r})" if by.get("tool") else ""
            print(f"  [{c['n']}] {c['name']}  {c['doc']}{hint}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
