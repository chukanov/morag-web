// Рендер темы без браузера: подставляем минимальный DOM и проверяем, что
// заголовок реально появляется, заставка отыгрывает и тема ставится один раз.
//     node tests/js/topic.test.mjs
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

class El {
  constructor(tag) {
    this.nodeType = 1; // как у настоящего элемента: el() отличает узлы от строк
    this.tagName = tag.toUpperCase();
    this.kids = [];
    this.attrs = {};
    // ⚠️ `setProperty` здесь не для галочки: переливание сцены держится на переменной `--p`
    // у каждого узла, и заглушка без этого метода роняла построение — а тест показывал
    // «в заставке пусто», уводя от причины.
    this.props = {};
    this.style = { setProperty: (k, v) => (this.props[k] = String(v)) };
    this.parentElement = null;
    this.classList = {
      _set: new Set(),
      add: (c) => this.classList._set.add(c),
      remove: (c) => this.classList._set.delete(c),
      contains: (c) => this.classList._set.has(c),
      toggle: (c, on) => (on ? this.classList._set.add(c) : this.classList._set.delete(c)),
    };
  }
  set className(v) {
    for (const c of String(v).split(/\s+/).filter(Boolean)) this.classList._set.add(c);
  }
  get className() {
    return [...this.classList._set].join(" ");
  }
  set textContent(v) {
    this.kids = [String(v)];
  }
  get textContent() {
    return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join("");
  }
  set innerHTML(v) {
    this.kids = [String(v)];
  }
  setAttribute(k, v) {
    this.attrs[k] = v;
  }
  removeAttribute(k) {
    delete this.attrs[k];
  }
  addEventListener(type, fn) {
    (this._on ||= {})[type] = fn;
  }
  append(...kids) {
    for (const kid of kids.flat()) {
      if (kid == null) continue;
      if (kid instanceof El) kid.parentElement = this;
      this.kids.push(kid);
    }
  }
  prepend(kid) {
    if (kid instanceof El) kid.parentElement = this;
    this.kids.unshift(kid);
  }
  remove() {
    const p = this.parentElement;
    if (p) p.kids = p.kids.filter((k) => k !== this);
    this.parentElement = null;
  }
  getBoundingClientRect() {
    return { width: 140, height: 20 };
  }
  get clientWidth() {
    return 800;
  }
  // упрощённые селекторы: берём последний класс из «.a .b» и ищем по нему
  querySelector(sel) {
    return this.find(String(sel).trim().split(/\s+/).pop().replace(/^\./, ""));
  }
  find(cls) {
    if (this.classList.contains(cls)) return this;
    for (const kid of this.kids) {
      if (kid instanceof El) {
        const hit = kid.find(cls);
        if (hit) return hit;
      }
    }
    return null;
  }
}

globalThis.document = {
  createElement: (t) => new El(t),
  createDocumentFragment: () => new El("#fragment"),
  createTextNode: (t) => String(t), // в заглушке текстовый узел — просто строка
  body: new El("body"), // на время сцены сюда ставится флаг «чат спрятан»
};
globalThis.getComputedStyle = () => ({ fontSize: "12px" });
globalThis.innerWidth = 1200;
globalThis.matchMedia = () => ({ matches: false });
globalThis.performance = globalThis.performance ?? { now: () => Date.now() };

// прогоняем анимацию быстро: подменяем время, чтобы дойти до финала за пару кадров
let clock = 0;
const pending = [];
globalThis.requestAnimationFrame = (fn) => pending.push(fn);
const tick = (dtMs) => {
  clock += dtMs;
  const batch = pending.splice(0, pending.length);
  for (const fn of batch) fn(clock);
};
const realNow = globalThis.performance.now.bind(globalThis.performance);
globalThis.performance.now = () => clock || realNow();

const { showTopic, clearTopic, hasTopic, reserveTopic, dropReservation } = await import(
  join(repo, "web/js/chat/topic.js")
);

let failures = 0;
const check = (name, fn) => {
  try {
    fn();
    console.log("  ✓", name);
  } catch (error) {
    failures += 1;
    console.log("  ✗", name, "\n     ", error.message.split("\n")[0]);
  }
};

console.log("резерв места под тему:");
const reserveStream = new El("main");

check("резерв ставит пустую полосу", () => {
  reserveTopic(reserveStream);
  assert.equal(reserveStream.kids.length, 1);
  assert.ok(reserveStream.kids[0].classList.contains("reserved"));
});
check("пустой резерв НЕ считается темой", () => {
  // иначе клиент решит, что тема уже есть, и перестанет просить её у сервера
  assert.equal(hasTopic(), false, "резерв выдал себя за готовую тему");
});
check("тема наполняет уже зарезервированное место, а не добавляет второе", () => {
  showTopic(reserveStream, { title: "Тема из резерва", summary: "…" }, {});
  assert.equal(reserveStream.kids.length, 1, "появилась вторая полоса — вёрстка сдвинется");
  assert.ok(!reserveStream.kids[0].classList.contains("reserved"));
  assert.ok(reserveStream.kids[0].find("topic-title").textContent.includes("из резерва"));
  assert.equal(hasTopic(), true);
});
check("резерв без темы освобождается", () => {
  clearTopic();
  const s = new El("main");
  reserveTopic(s);
  dropReservation();
  assert.equal(s.kids.length, 0, "пустая полоса осталась висеть");
});

check("сцена играет ПОКА ждём тему, а не по таймеру", () => {
  clearTopic();
  const s = new El("main");
  reserveTopic(s);
  const strip = s.kids[0];

  // тема считается долго — сцена должна продолжать идти, а не оборваться
  for (let i = 0; i < 200; i++) tick(40); // 8 секунд ожидания
  assert.ok(strip.find("fx-art").textContent.length > 200, "сцена оборвалась, не дождавшись темы");
  assert.ok(strip.classList.contains("thinking"), "подпись должна оставаться «определяю»");

  // тема пришла — сцена доигрывает и уступает место заголовку
  showTopic(s, { title: "Долгожданная тема", summary: "…" }, {});
  for (let i = 0; i < 200 && strip.classList.contains("thinking"); i++) tick(40);
  assert.ok(!strip.classList.contains("thinking"), "сцена не завершилась после прихода темы");
  assert.ok(strip.find("topic-title").textContent.includes("Долгожданная"));
});

clearTopic();
console.log("\nтема разговора:");
const stream = new El("main");
const opened = [];

check("рендер не падает", () => {
  showTopic(
    stream,
    { title: "Спор о замене программистов", summary: "О прогнозах и влиянии ИИ.", records: ["1-18", "2-5"] },
    { onOpenRecord: (id) => opened.push(id) }
  );
});

const topic = stream.kids[0];
check("заголовок добавлен в начало ленты", () => {
  assert.ok(topic instanceof El, "в ленте нет узла темы");
  assert.ok(topic.classList.contains("topic"));
});
check("текст темы на месте", () => {
  assert.ok(topic.find("topic-title").textContent.includes("замене программистов"));
  assert.ok(topic.find("topic-gloss").textContent.includes("прогнозах"));
});
check("выпуски стали чипами", () => {
  const eps = topic.find("topic-eps");
  const chips = eps.kids.filter((k) => k instanceof El);
  assert.equal(chips.length, 2);
  chips[0]._on.click();
  assert.deepEqual(opened, ["1-18"]);
});
check("до заставки заголовок скрыт", () => {
  assert.equal(topic.find("topic-title").style.opacity, "0");
  assert.ok(topic.classList.contains("thinking"));
});
check("заставка рисует ASCII-сцену", () => {
  tick(100);
  const art = topic.find("fx-art");
  assert.ok(art.textContent.length > 200, `в заставке пусто (${art.textContent.length} симв.)`);
  // ⚠️ Строки сцены — ОТДЕЛЬНЫЕ узлы, а не переносы в тексте: символы разбиты по блокам ради
  // переливания, и перенос теперь рисует CSS. Проверяем узлы, а не «\n» в строке.
  const rows = art.kids.filter((k) => k?.classList?.contains?.("fx-row"));
  assert.ok(rows.length > 5, `сцена должна быть многострочной (строк: ${rows.length})`);
  // Фаза у каждого блока своя — иначе волна выродится в общее мигание.
  const phases = new Set(rows.flatMap((r) => r.kids.map((c) => c?.props?.["--p"])));
  assert.ok(phases.size > 4, `переливание без волны: разных фаз ${phases.size}`);
});
check("после заставки тема проявляется", () => {
  // крутим часы, пока сцена не доиграет — не завязываемся на конкретную длительность
  for (let i = 0; i < 400 && topic.classList.contains("thinking"); i++) tick(40);
  assert.ok(!topic.classList.contains("thinking"), "подпись осталась в режиме «определяю»");
  assert.equal(topic.find("topic-kicker").textContent, "Тема разговора");
});
check("тема ставится один раз за сессию", () => {
  showTopic(stream, { title: "Другая тема", summary: "" }, {});
  assert.equal(stream.kids.filter((k) => k instanceof El && k.classList.contains("topic")).length, 1);
});
check("новый чат уносит тему", () => {
  clearTopic();
  assert.equal(stream.kids.filter((k) => k instanceof El && k.classList.contains("topic")).length, 0);
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
