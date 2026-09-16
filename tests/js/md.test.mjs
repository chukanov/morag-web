// Проверка рендерера markdown на РЕАЛЬНОМ ответе движка (из записанной фикстуры).
// Браузера нет — подставляем минимальный DOM и смотрим на получившееся дерево.
//     node tests/js/md.test.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");

// --- крошечный DOM ---------------------------------------------------------
class Node_ {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.kids = [];
    this.attrs = {};
  }
  set textContent(value) {
    this.kids = [String(value)];
  }
  get textContent() {
    return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join("");
  }
  set className(v) {
    this.attrs.class = v;
  }
  set innerHTML(v) {
    this.kids = [String(v)];
  }
  setAttribute(k, v) {
    this.attrs[k] = v;
  }
  addEventListener() {}
  append(...kids) {
    for (const kid of kids.flat()) {
      if (kid == null) continue;
      if (kid instanceof Frag) this.kids.push(...kid.kids);
      else this.kids.push(kid);
    }
  }
}
class Frag extends Node_ {
  constructor() {
    super("#fragment");
  }
}
globalThis.document = {
  createElement: (tag) => new Node_(tag),
  createDocumentFragment: () => new Frag(),
  createTextNode: (t) => String(t),
};

const { renderMarkdown, claimsByRef } = await import(join(repo, "web/js/chat/md.js"));

// --- текст ответа из фикстуры ---------------------------------------------
function answerFromFixture(name) {
  const raw = readFileSync(join(repo, "tests/fixtures", name), "utf8");
  let text = "";
  for (const line of raw.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice(6);
    if (payload.trim() === "[DONE]") continue;
    const obj = JSON.parse(payload);
    const content = obj?.choices?.[0]?.delta?.content;
    if (content) text += content;
  }
  return text;
}

const tags = (frag) => frag.kids.filter((k) => k instanceof Node_).map((k) => k.tagName);
const flat = (frag) => frag.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join("\n");

// --- проверки --------------------------------------------------------------
let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log("  ✓", name);
  } catch (error) {
    failures += 1;
    console.log("  ✗", name, "\n     ", error.message.split("\n")[0]);
  }
}

console.log("рендер реального ответа (engine_h200.sse):");
const answer = answerFromFixture("engine_h200.sse");
const seen = new Set();
const out = renderMarkdown(answer, {
  makeRef: (n) => {
    seen.add(n);
    const a = new Node_("a");
    a.textContent = String(n);
    return a;
  },
});

check("текст не пустой", () => assert.ok(flat(out).length > 500));
check("звёздочки markdown не остались", () => assert.ok(!flat(out).includes("**")));
check("подзаголовки распознаны", () => {
  const h3 = tags(out).filter((t) => t === "H3").length;
  assert.ok(h3 >= 4, `ожидал ≥4 h3, получил ${h3}`);
});
check("есть абзацы", () => assert.ok(tags(out).filter((t) => t === "P").length >= 2));
check("список собран", () => {
  const lists = out.kids.filter((k) => k instanceof Node_ && ["UL", "OL"].includes(k.tagName));
  assert.ok(lists.length >= 1, "не нашёл списка");
  assert.ok(lists.some((l) => l.kids.length >= 2), "список из одного пункта — подозрительно");
});
check("маркеры [N] стали ссылками", () => {
  assert.ok(seen.size >= 5, `ссылок на цитаты: ${seen.size}`);
  assert.ok(!flat(out).match(/\[\d+\]/), "остались неразобранные [N]");
});
check("подзаголовок не склеен с телом", () => {
  const first = out.kids.find((k) => k instanceof Node_ && k.tagName === "H3");
  assert.ok(first.textContent.length < 90, `слишком длинный h3: ${first.textContent.slice(0, 60)}…`);
});

console.log("\nсинтетические случаи:");
const cases = renderMarkdown(
  "Обычный текст с **жирным** и *курсивом* и `кодом`.\n\n" +
    "**Заголовок раздела**\nТело раздела.\n\n" +
    "- пункт один\n- пункт два\n\n" +
    "1. первый\n2. второй",
  { makeRef: () => null }
);
check("структура блоков", () =>
  assert.deepEqual(tags(cases), ["P", "H3", "P", "UL", "OL"])
);
check("инлайн-разметка снята", () => {
  const p = cases.kids[0];
  assert.ok(p.textContent.includes("жирным") && !p.textContent.includes("*"));
});
check("неизвестная цитата остаётся текстом", () => {
  const frag = renderMarkdown("Ответ [7] без карточки.", { makeRef: () => null });
  assert.ok(frag.kids[0].textContent.includes("[7]"));
});

console.log("\nутверждения ответа по цитатам:");

check("берётся предложение с маркером, а не весь абзац", () => {
  const claims = claimsByRef(
    "Первое предложение без ссылок. Малых оценил объём в 640 ГБ [1]. И третье."
  );
  assert.deepEqual(claims.get(1), ["Малых оценил объём в 640 ГБ [1]."]);
});

check("маркер в конце пункта списка не утягивает соседний пункт", () => {
  // модель почти всегда пишет списком — на этом «предложение» и уползало
  const claims = claimsByRef("*   Первый пункт про H200 [2]\n*   Второй пункт про другое [3]");
  assert.deepEqual(claims.get(2), ["Первый пункт про H200 [2]"]);
  assert.deepEqual(claims.get(3), ["Второй пункт про другое [3]"]);
});

check("одна цитата подпирает несколько мест — собираем все", () => {
  const claims = claimsByRef("Раз про карты [1]. Два про них же [1]. Три без ссылки.");
  assert.equal(claims.get(1).length, 2);
});

check("повторы не дублируются, а длинное режется", () => {
  const long = `${"очень длинное утверждение ".repeat(20)}[4].`;
  const claims = claimsByRef(long);
  assert.ok(claims.get(4)[0].length <= 260, claims.get(4)[0].length);
  assert.ok(claims.get(4)[0].endsWith("…"));
});

check("несколько маркеров подряд — общее утверждение у каждого", () => {
  const claims = claimsByRef("Наценка могла бы вырасти до 5000% [1][3].");
  assert.equal(claims.get(1)[0], claims.get(3)[0]);
});

// --- адреса становятся ссылками -------------------------------------------
// Замерено на живом ответе: просили markdown-ссылку — модель написала `**адрес**`
// жирным. Значит ловим сам адрес, а не надеемся на её разметку.
const links = (frag) => {
  const found = [];
  const walk = (n) => {
    for (const k of n.kids || []) {
      if (typeof k === "string") continue;
      if (k.tagName === "A") found.push({ href: k.attrs.href, text: k.textContent });
      walk(k);
    }
  };
  walk(frag);
  return found;
};

check("голый адрес становится ссылкой", () => {
  const got = links(renderMarkdown("Код на https://github.com/catonmoon/morag."));
  assert.equal(got.length, 1);
  assert.equal(got[0].href, "https://github.com/catonmoon/morag");
});

check("точка в конце предложения не уезжает в адрес", () => {
  const [a] = links(renderMarkdown("Смотри https://example.com/x."));
  assert.ok(!a.href.endsWith("."), a.href);
});

check("адрес внутри жирного тоже ссылка", () => {
  const got = links(renderMarkdown("Код: **https://github.com/catonmoon/morag**"));
  assert.equal(got.length, 1, JSON.stringify(got));
  assert.equal(got[0].href, "https://github.com/catonmoon/morag");
});

check("markdown-ссылка по-прежнему работает и подпись остаётся своей", () => {
  const [a] = links(renderMarkdown("[github.com/catonmoon/morag](https://github.com/catonmoon/morag)"));
  assert.equal(a.text, "github.com/catonmoon/morag");
  assert.equal(a.href, "https://github.com/catonmoon/morag");
});

check("без схемы адресом не считаем — иначе им станет config.yml/x", () => {
  assert.equal(links(renderMarkdown("правь app/config.yml/секцию и жди")).length, 0);
});

check("в `коде` адрес остаётся текстом", () => {
  assert.equal(links(renderMarkdown("запусти `curl https://example.com/a`")).length, 0);
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
