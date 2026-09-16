// Витрина: карточка на каждое пространство, в порядке конфига.
//     node tests/js/hub.test.mjs
//
// Проверяем не красоту, а два обещания, на которых держится мультикорпусность:
// карточка ведёт ОБЫЧНОЙ ссылкой (переход между пространствами — полная перезагрузка, потому
// что конфиг, индекс, плеер и история чата у каждого свои), и ни одно пространство не теряется.
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    // ⚠️ Без nodeType помощник `el()` считает узел строкой и заворачивает его в текстовый
    // узел — в дереве вместо карточки оказывается «[object Object]».
    this.nodeType = 1;
    this.kids = [];
    this.attrs = {};
    this.textContent = "";
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  append(...kids) { for (const k of kids) if (k != null) this.kids.push(k); }
  replaceChildren(...kids) { this.kids = kids.filter((k) => k != null); }
  get classList() {
    return { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false };
  }
  get style() { return {}; }
  querySelectorAll() { return []; }
  // Текст поддерева: узлы-строки приходят от createTextNode, элементы — рекурсией.
  get text() {
    const kids = this.kids.map((k) => (typeof k.text === "string" ? k.text : k.textContent || ""));
    return [this.textContent, ...kids].filter(Boolean).join(" ").trim();
  }
}

const nodes = {};
globalThis.document = {
  createElement: (t) => new El(t),
  createTextNode: (t) => ({ nodeType: 3, textContent: String(t), text: String(t) }),
  documentElement: { style: { setProperty: () => {} } },
  querySelector: (sel) => (nodes[sel] = nodes[sel] || new El("div")),
  title: "",
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

const { renderHub } = await import(join(repo, "web/js/hub.js"));

const data = {
  hub: { title: "Записи", tagline: "{записей} в разделах", about: "<p>о сайте</p>", theme: {} },
  corpora: [
    { slug: "one", title: "Первое", tagline: "", records_count: 75, hours: 61.2, accent: "#111", chat_enabled: true },
    { slug: "two", title: "Второе", tagline: "", records_count: 2, hours: 1.8, accent: "#222", chat_enabled: false },
  ],
};

const mount = new El("div");
renderHub(data, { mount });

assert.equal(mount.kids.length, 2, "карточка должна быть на каждое пространство");
assert.equal(mount.kids[0].attrs.href, "/one", "карточка обязана быть обычной ссылкой");
assert.equal(mount.kids[1].attrs.href, "/two");
assert.equal(mount.kids[0].className, "space-card");
assert.ok(mount.kids[0].text.includes("75 записей"), mount.kids[0].text);
assert.ok(mount.kids[0].text.includes("61 час"), mount.kids[0].text);
// Пространство без поиска обязано сказать об этом ЗДЕСЬ: иначе отсутствие кнопки «Спросить»
// внутри читается как поломка, а не как решение.
assert.ok(mount.kids[1].text.includes("без поиска"), mount.kids[1].text);
assert.ok(!mount.kids[0].text.includes("без поиска"));

// Живое число вместо плейсхолдера: в конфиге число писать нельзя — устареет молча.
assert.equal(nodes["#hub-tagline"].textContent, "77 записей в разделах");

// Вырожденный случай: одно пространство — витрина всё равно рисуется (перенаправление живёт
// в main.js, а сам рисовальщик не вправе предполагать, что пространств много).
const single = new El("div");
renderHub({ hub: {}, corpora: [data.corpora[0]] }, { mount: single });
assert.equal(single.kids.length, 1);

console.log("hub: ок");
