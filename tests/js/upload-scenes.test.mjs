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
    this.classList = {
      _s: new Set(),
      add: (c) => this.classList._s.add(c),
      remove: (c) => this.classList._s.delete(c),
      contains: (c) => this.classList._s.has(c),
      toggle: (c, on) => (on ? this.classList._s.add(c) : this.classList._s.delete(c)),
    };
  }
  set className(v) { for (const c of String(v).split(/\s+/).filter(Boolean)) this.classList._s.add(c); }
  set textContent(v) { this.kids = [String(v)]; }
  get textContent() { return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join(""); }
  get children() { return this.kids.filter((k) => k instanceof El); }
  get lastElementChild() { return this.children.at(-1) || null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener() {}
  append(...kids) {
    for (const k of kids.flat()) {
      if (k == null) continue;
      if (k instanceof El) k.parentElement = this;
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
  getContext() { return null; }
}

globalThis.document = {
  createElement: (t) => new El(t),
  createTextNode: (t) => String(t),
  documentElement: new El("html"),
};
globalThis.matchMedia = () => ({ matches: false });
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "#E4A04B" });
globalThis.requestAnimationFrame = (fn) => { fn(0); return 1; };
globalThis.performance = { now: () => 0 };

const { budget, state, MIN_RATE, MAX_RATE } = await import(join(repo, "tools/ui/play.js"));
const { fixes } = await import(join(repo, "tools/ui/fixes.js"));

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
  const scene = fixes(root);
  scene.apply({ t: "stage.start", stage: "final-round" });

  scene.apply({ t: "turn.fix", turn: 0, start: 61, was: "эйр флоу", now: "Airflow", ok: true, why: "" });
  scene.apply({ t: "turn.fix", turn: 1, start: 150, was: "H200", now: "H100", ok: false, why: "number" });
  scene.apply({ t: "turn.fix", turn: 2, start: 30, was: "Ковалёв", now: "Ковалев", ok: false,
                why: "breaks_term", term: "Мария Ковалёва" });

  const list = root.children.find((k) => k.classList.contains("fx-list"));
  const cards = list.children;
  assert.equal(cards.length, 3);
  // Порядок — по завершению: последняя пришедшая сверху. У каждой карточки тайм-код, иначе
  // человек решит, что запись обрабатывают задом наперёд.
  assert.match(cards[0].textContent, /0:30/);
  assert.equal(cards[0].attrs["data-ok"], "0");
  assert.match(cards[0].textContent, /сломало бы известный термин «Мария Ковалёва»/);
  assert.match(cards[1].textContent, /меняет число/);
  assert.equal(cards[2].attrs["data-ok"], "1");
  assert.ok(!/why/.test(cards[2].textContent), "у принятой замены причины нет");

  assert.deepEqual(scene.state(), { applied: 1, dropped: 2, turns: 0, cards: 3 });

  // Полоска реплик строится ОДИН раз и дальше только перекрашивается.
  scene.apply({ t: "turn.done", turn: 0, start: 61, n: 5, changed: true });
  const ticks = root.children.find((k) => k.classList.contains("fx-ticks"));
  assert.equal(ticks.children.length, 5);
  assert.ok(ticks.children[0].classList.contains("changed"));
  scene.apply({ t: "turn.done", turn: 1, start: 150, n: 5, changed: false });
  assert.equal(ticks.children.length, 5, "второе событие узлов не добавляет");
  assert.ok(ticks.children[1].classList.contains("kept"));
}
{
  // Список не лог: старое уезжает, иначе за девять минут накопится четыре сотни узлов.
  const root = new El("div");
  const scene = fixes(root);
  for (let i = 0; i < 60; i++) {
    scene.apply({ t: "turn.fix", turn: i, start: i, was: "а", now: "б", ok: true, why: "" });
  }
  assert.ok(scene.state().cards <= 40, `карточек ${scene.state().cards}`);
  assert.equal(scene.state().applied, 60, "счётчик считает ВСЕ, а не видимые");
}

console.log("ok upload-scenes");
