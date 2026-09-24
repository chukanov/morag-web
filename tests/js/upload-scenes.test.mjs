// Показ работы в окне загрузки: темп, честность подписи и сцена исправлений.
//     node tests/js/upload-scenes.test.mjs
//
// ⚠️ Анимация проверяется БЕЗ прогона расшифровки — по записанной трассе событий. Иначе каждая
// правка шрифта стоила бы двадцати минут транскрибации, и её просто не стали бы делать.
// ⚠️ Заглушка DOM здесь простая и `dataset` НЕ знает — в сценах только `setAttribute`.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.kids = [];
    this.attrs = {};
    this.style = { setProperty() {} };
    // Минимальная геометрия: сцена листает окно текста, и проверяется именно то, что она
    // листает (сколько раз), а не куда: раскладки в заглушке нет и быть не может.
    this.clientHeight = 200;
    this.scrollHeight = 900;      // текста больше, чем окно — иначе прокрутку не проверить
    this.scrolls = 0;
    this._scroll = 0;
    this._on = {};
    this.classList = {
      _s: new Set(),
      add: (c) => this.classList._s.add(c),
      remove: (c) => this.classList._s.delete(c),
      contains: (c) => this.classList._s.has(c),
      toggle: (c, on) => (on ? this.classList._s.add(c) : this.classList._s.delete(c)),
    };
  }
  set className(v) { for (const c of String(v).split(/\s+/).filter(Boolean)) this.classList._s.add(c); }
  set textContent(v) { const t = new TextNode(v); t.parentElement = this; this.kids = [t]; }
  get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); }
  get children() { return this.kids.filter((k) => k instanceof El); }
  get childNodes() { return this.kids; }
  get lastElementChild_() { return this.children.at(-1) || null; }
  get lastElementChild() { return this.children.at(-1) || null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this._on[type] = this._on[type] || []).push(fn); }
  getBoundingClientRect() { return { top: this._top || 0, left: 0 }; }
  get scrollTop() { return this._scroll; }
  // ⚠️ Событие шлётся СИНХРОННО — строже, чем в браузере (там оно придёт перед кадром):
  // проверка «это не я прокрутил» не имеет права зависеть от момента доставки.
  set scrollTop(v) {
    this._scroll = Number.isFinite(v) ? v : 0;
    this.scrolls += 1;
    for (const fn of this._on.scroll || []) fn({ target: this });
  }
  append(...kids) {
    for (const k of kids.flat()) {
      if (k == null) continue;
      if (typeof k === "string") { this.kids.push(new TextNode(k)); this.kids.at(-1).parentElement = this; continue; }
      k.parentElement = this;
      this.kids.push(k);
    }
  }
  prepend(k) { if (k instanceof El) k.parentElement = this; this.kids.unshift(k); }
  replaceChildren(...kids) { this.kids = []; this.append(...kids); }
  // ⚠️ Узел обязан уходить У РОДИТЕЛЯ: без этого обрезка списка («пока карточек больше сорока —
  // удаляй последнюю») превращается в бесконечный цикл, и тест просто виснет.
  remove() {
    const p = this.parentElement;
    if (p) p.kids = p.kids.filter((k) => k !== this);
    this.parentElement = null;
  }
  replaceWith(node) {
    const p = this.parentElement;
    if (!p) return;
    p.kids[p.kids.indexOf(this)] = node;
    node.parentElement = p;
    this.parentElement = null;
  }
  getContext() { return null; }
}

// ⚠️ Правка идёт ВНУТРИ текста, значит заглушке нужны настоящие текстовые узлы с `splitText`:
// на строках это не проверить, и сцена бы «работала» в тесте, ничего не меняя на экране.
class TextNode {
  constructor(t) { this.nodeType = 3; this.textContent = String(t); this.parentElement = null; }
  splitText(i) {
    const rest = new TextNode(this.textContent.slice(i));
    this.textContent = this.textContent.slice(0, i);
    const p = this.parentElement;
    if (p) { p.kids.splice(p.kids.indexOf(this) + 1, 0, rest); rest.parentElement = p; }
    return rest;
  }
  replaceWith(node) {
    const p = this.parentElement;
    if (!p) return;
    p.kids[p.kids.indexOf(this)] = node;
    node.parentElement = p;
  }
}

globalThis.document = {
  createElement: (t) => new El(t),
  createTextNode: (t) => new TextNode(t),
  documentElement: { getAttribute: () => "dark" },
};
globalThis.matchMedia = () => ({ matches: false });
globalThis.window = globalThis;
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "#E4A04B" });
// ⚠️ Кадры — УПРАВЛЯЕМЫЕ, и вызывать колбэк синхронно прямо из `requestAnimationFrame` нельзя:
// сцена сбрасывает свою защёлку внутри кадра, а синхронный вызов возвращает номер уже ПОСЛЕ
// сброса — цикл встаёт навсегда, и сцена замирает после первой правки. Ровно на этом и попались.
let CLOCK = 0;
const FRAMES = [];
globalThis.requestAnimationFrame = (fn) => FRAMES.push(fn);
globalThis.setTimeout = (fn) => { fn(); return 1; };
/** Прокрутить n кадров, каждый на 500 мс вперёд — быстрее любого темпа сцены. */
function flush(n = 12) {
  for (let i = 0; i < n; i++) {
    const batch = FRAMES.splice(0, FRAMES.length);
    CLOCK += 500;
    for (const fn of batch) fn(CLOCK);
  }
}
globalThis.performance = { now: () => 0 };

const { budget, state, MIN_RATE, MAX_RATE } = await import(join(repo, "tools/ui/play.js"));
const { textScene } = await import(join(repo, "tools/ui/text.js"));

// --- темп ------------------------------------------------------------------------------------

{
  // Пачка в 500 событий (например, клиент отстал и догоняет) разбирается быстро, но не за кадр:
  // иначе окно моргнёт и человек ничего не увидит.
  const per = budget(500, 1 / 60);
  assert.ok(per <= Math.ceil(MAX_RATE / 60) + 1, `за кадр не больше потолка, вышло ${per}`);
  assert.ok(500 / (per * 60) < 5, "но и не за минуту: пачка разбирается за секунды");
}
{
  // Мало событий — всё равно живо: пасс-2 отдаёт примерно кусок в секунду, и показ не должен
  // выглядеть замершим.
  assert.equal(budget(2, 1 / 60), Math.max(1, Math.ceil((MIN_RATE * 1) / 60)));
  assert.ok(budget(2, 0.2) >= 1);
}
{
  // «Меньше движения» — никакого темпа вовсе: применяем пачкой и рисуем конечное состояние.
  assert.equal(budget(37, 1 / 60, { reduced: true }), 37);
  assert.equal(budget(0, 1 / 60), 0);
}

// --- честность -------------------------------------------------------------------------------

{
  // Полоса двигается ТОЛЬКО по настоящему счётчику: это прямая замена прежней, которая искала
  // в логе процент, не находила и стояла на 30 % всю расшифровку.
  const a = state({ stage: "pass2", counter: { i: 0, n: 195 }, lastAt: 0, now: 0 });
  const b = state({ stage: "pass2", counter: { i: 100, n: 195 }, lastAt: 0, now: 0 });
  assert.ok(b.pct > a.pct, "счётчик кусков двигает полосу");
  const c = state({ stage: "pass2", counter: null, lastAt: 0, now: 2 });
  assert.equal(c.pct, a.pct, "без счётчика полоса стоит — и это честно");
}
{
  const live = state({ stage: "pass2", counter: { i: 84, n: 195 }, lastAt: 10, now: 11 });
  assert.equal(live.mood, "работает");
  assert.match(live.say, /84 из 195/);

  const thinking = state({ stage: "pass1", lastAt: 10, now: 25 });
  assert.equal(thinking.mood, "считает", "стадия молчит по своей природе — так и говорим");
  assert.match(thinking.say, /обычно/);

  const lost = state({ stage: "final-round", lastAt: 10, now: 70 });
  assert.equal(lost.mood, "молчит");
  assert.match(lost.say, /без вестей/);

  const failed = state({ error: "Не получилось: стек не отвечает" });
  assert.equal(failed.mood, "ошибка");
  assert.equal(failed.pct, null, "у ошибки полосы нет вовсе");
}

// --- сцена исправлений -------------------------------------------------------------------------

{
  const root = new El("div");
  const scene = textScene(root);
  scene.apply({ t: "stage.start", stage: "final-round" });

  // Реплика, над которой идёт работа, — и правки прямо в ней.
  scene.apply({ t: "turn.text", turn: 0, start: 61, whole: true,
                text: "Мы берём эйр флоу и ставим его в H200, а звонил Ковалёв." });
  scene.apply({ t: "turn.fix", turn: 0, start: 61, was: "эйр флоу", now: "Airflow", ok: true, why: "" });
  scene.apply({ t: "turn.fix", turn: 1, start: 150, was: "H200", now: "H100", ok: false, why: "number" });
  scene.apply({ t: "turn.fix", turn: 2, start: 30, was: "Ковалёв", now: "Ковалев", ok: false,
                why: "breaks_term", term: "Мария Ковалёва" });
  flush();

  // ⚠️ В тексте остаётся ИСПРАВЛЕННОЕ слово, а прежнее уходит в слой над текстом и
  // всплывает по наведению: конец работы — готовая расшифровка, а не лист корректуры.
  assert.equal(scene.state().edits, 3, "все три правки легли в текст");

  const all = [];
  (function walk(node) {
    for (const kid of node.kids || []) if (kid instanceof El) { all.push(kid); walk(kid); }
  })(root);
  const cls = (c) => all.filter((n) => n.classList.contains(c)).map((n) => n.textContent);

  // ⚠️ Узлы — обычные inline-`span`: у `ruby` и `inline-block` своя высота и свои точки переноса,
  // из-за них строка с правкой становилась выше соседних.
  assert.equal(all.filter((n) => n.classList.contains("ed")).length, 3, "каждая правка — свой узел");
  assert.ok(all.filter((n) => n.classList.contains("ed")).every((n) => n.tagName === "SPAN"),
            "и это обычный span, а не ruby");
  assert.deepEqual(cls("ed-now"), ["Airflow"], "принятая правка — это новое слово в тексте");
  assert.deepEqual(cls("ed-kept"), ["H200", "Ковалёв"], "отвергнутая текст не меняет вовсе");

  const over = cls("ed-old");
  assert.equal(over.length, 3, "слой есть у каждой");
  assert.match(over[0], /было: эйр флоу/, "у принятой в слое — старое слово");
  assert.match(over[1], /не принято: H100/, "у отвергнутой — что предлагали…");
  assert.match(over[1], /меняет число/, "…и почему не взяли");
  assert.match(over[2], /сломало бы известный термин.*Мария Ковалёва/, "и какой именно термин");
  assert.ok(!all.some((n) => n.classList.contains("ed-i")), "знака ⓘ больше нет — причина в том же слое");

  scene.apply({ t: "turn.done", turn: 0, start: 61, n: 5, changed: true });
  assert.equal(scene.state().turns, 5);
}
{
  // ⚠️ Слежение за правкой не должно выключаться НАШЕЙ же прокруткой. `scrollTop = …`
  // поднимает событие `scroll`, и пока сцена считала его человеческим, она после первой же замены
  // замирала на четыре секунды, и корректура ложилась за краем окна — человек не видел ни одной.
  const root = new El("div");
  const scene = textScene(root);
  scene.apply({ t: "stage.start", stage: "final-round" });
  scene.apply({ t: "turn.text", turn: 0, start: 10,
                text: "Мы берём эйр флоу, а в очереди кафка." });
  const body = root.children[1];
  const before = body.scrolls;
  scene.apply({ t: "turn.fix", turn: 0, start: 10, was: "эйр флоу", now: "Airflow", ok: true });
  flush();
  const one = body.scrolls;
  scene.apply({ t: "turn.fix", turn: 0, start: 10, was: "кафка", now: "Kafka", ok: true });
  flush();
  assert.ok(one > before, "к первой правке сцена листает");
  assert.ok(body.scrolls > one, "и ко второй тоже — своя прокрутка слежение не выключает");
  assert.equal(scene.state().edits, 2, "и обе правки легли в текст");

  // А вот НАСТОЯЩИЙ жест человека слежение останавливает: он читает своё место, и увозить
  // его оттуда нельзя.
  const two = body.scrolls;
  for (const fn of body._on.wheel || []) fn({});
  scene.apply({ t: "turn.fix", turn: 0, start: 10, was: "очереди", now: "очередь", ok: false, why: "empty" });
  flush();
  assert.equal(body.scrolls, two, "после жеста человека сцена за ним не бегит");
  assert.equal(scene.state().edits, 3, "но правку в текст всё равно ставит");
}
{
  // Текст печатается ПО МЕРЕ появления: событие пришло одно, а читается оно кадрами.
  const root = new El("div");
  const scene = textScene(root);
  scene.apply({ t: "stage.start", stage: "pass2" });
  scene.apply({ t: "chunk.done", i: 1, raw: "Смотрите, здесь у нас обычная очередь." });
  const first = scene.state().text.length;
  flush(1);
  const mid = scene.state().text.length;
  flush(60);
  const done = scene.state().text.length;
  assert.ok(first < mid && mid < done, `печать идёт кадрами: ${first} → ${mid} → ${done}`);
  // ⚠️ Пишущийся текст сам держится КОНЦА: смотрят ради того, что появляется прямо
  // сейчас, а оно приходит снизу.
  assert.equal(root.children[1].scrollTop, 900, "окно догнало конец текста");
  assert.match(scene.state().text, /обычная очередь/);
  assert.equal(scene.state().mode, "writing");
}

console.log("ok upload-scenes");
