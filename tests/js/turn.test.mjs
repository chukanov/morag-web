// Ход диалога целиком: кадры потока → разметка, карточки, кнопки.
// Ловит в том числе «мёртвую зону» (объявления после return не выполняются).
//     node tests/js/turn.test.mjs
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
    this.style = {};
    this.dataset = {};
    this.parentElement = null;
    const set = new Set();
    this.classList = {
      _set: set,
      add: (...c) => c.forEach((x) => set.add(x)),
      remove: (...c) => c.forEach((x) => set.delete(x)),
      contains: (c) => set.has(c),
      toggle: (c, on) => (on ? set.add(c) : set.delete(c)),
    };
  }
  set className(v) {
    String(v).split(/\s+/).filter(Boolean).forEach((c) => this.classList._set.add(c));
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
  get innerText() {
    return this.textContent;
  }
  setAttribute(k, v) {
    this.attrs[k] = v;
  }
  removeAttribute(k) {
    delete this.attrs[k];
  }
  toggleAttribute(k, on) {
    if (on) this.attrs[k] = "";
    else delete this.attrs[k];
  }
  hasAttribute(k) {
    return k in this.attrs;
  }
  addEventListener(type, fn) {
    (this._on ||= {})[type] = fn;
  }
  append(...kids) {
    for (const kid of kids.flat()) {
      if (kid == null) continue;
      if (kid instanceof El) {
        // Настоящий DOM при вставке ПЕРЕНОСИТ узел, а не копирует: на этом
        // держится переезд карточки во врезку и обратно в список.
        if (kid.parentElement) {
          kid.parentElement.kids = kid.parentElement.kids.filter((k) => k !== kid);
        }
        if (kid.tagName === "#FRAGMENT") {
          kid.kids.forEach((k) => this.append(k));
          continue;
        }
        kid.parentElement = this;
      }
      this.kids.push(kid);
    }
  }
  prepend(k) {
    this.kids.unshift(k);
  }
  replaceChildren(...kids) {
    this.kids = [];
    this.append(...kids);
  }
  insertBefore(node) {
    this.kids.push(node);
  }
  remove() {
    if (this.parentElement) this.parentElement.kids = this.parentElement.kids.filter((k) => k !== this);
  }
  get lastElementChild() {
    return [...this.kids].reverse().find((k) => k instanceof El) || null;
  }
  get firstChild() {
    return this.kids[0] || null;
  }
  get children() {
    return this.kids.filter((k) => k instanceof El);
  }
  // упрощённые селекторы: берём последний класс из «.a .b» и ищем по нему
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel) {
    const cls = String(sel).trim().split(/\s+/).pop().replace(/^\./, "");
    return this.findAll(cls).filter((n) => n !== this);
  }
  scrollIntoView() {}
  // Врезка с цитатой встаёт ПОСЛЕ абзаца — заглушке нужен этот способ вставки.
  after(node) {
    const parent = this.parentElement;
    if (!parent) return;
    const at = parent.kids.indexOf(this);
    parent.kids.splice(at + 1, 0, node);
    node.parentElement = parent;
  }
  contains(node) {
    if (node === this) return true;
    return this.kids.some((k) => k instanceof El && k.contains(node));
  }
  getBoundingClientRect() {
    return { width: 120, height: 20 };
  }
  get clientWidth() {
    return 600;
  }
  get offsetTop() {
    return 0;
  }
  get offsetHeight() {
    return 20;
  }
  find(cls) {
    if (this.classList.contains(cls)) return this;
    for (const kid of this.kids) if (kid instanceof El) {
      const hit = kid.find(cls);
      if (hit) return hit;
    }
    return null;
  }
  findAll(cls, acc = []) {
    if (this.classList.contains(cls)) acc.push(this);
    for (const kid of this.kids) if (kid instanceof El) kid.findAll(cls, acc);
    return acc;
  }
  findTags(tag, acc = []) {
    if (this.tagName === tag.toUpperCase()) acc.push(this);
    for (const kid of this.kids) if (kid instanceof El) kid.findTags(tag, acc);
    return acc;
  }
}

// Врезка закрывается по клику мимо и по Esc — заглушке нужны эти слушатели,
// иначе поведение в тестах просто не исполняется.
const docHandlers = {};
globalThis.document = {
  createElement: (t) => new El(t),
  createDocumentFragment: () => new El("#fragment"),
  createTextNode: (t) => String(t),
  body: new El("body"),
  addEventListener: (type, fn) => ((docHandlers[type] ||= []).push(fn)),
  removeEventListener: (type, fn) => {
    docHandlers[type] = (docHandlers[type] || []).filter((f) => f !== fn);
  },
};
const fireDoc = (type, event) => (docHandlers[type] || []).forEach((fn) => fn(event));
globalThis.matchMedia = () => ({ matches: false });
globalThis.Audio = class {
  constructor() {
    this.paused = true;
  }
  addEventListener() {}
  play() {
    return Promise.resolve();
  }
  pause() {}
};
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
globalThis.getComputedStyle = () => ({ fontSize: "12px" });

const { createTurn } = await import(join(repo, "web/js/chat/view.js"));

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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

console.log("ход диалога:");

const turn = createTurn("Заменит ли ИИ программистов?", {});
const citation = {
  type: "citation",
  n: 1,
  label: "Заменит ли ИИ нас · 08:15 · Даниэль",
  url: "/api/media/2026-07-17-spark.mp4?slug=demo",
  text: "[08:15] [Даниэль] Рутину съедает, ответственность — нет.",
  rec: "2026-07-17-spark",
  sec: 495,
  end: 560,
};

check("статус добавляется", () => {
  turn.addStatus("🔍 [ИИ программисты] по всей базе");
  assert.ok(turn.node.find("st-text").textContent.includes("по всей базе"));
  assert.equal(turn.node.find("st-ico").textContent, "🔍", "значок берётся из статуса");
});

check("токены не роняют ход", () => {
  // именно здесь ловилась «мёртвая зона»: scheduleRender падал на renderTimer
  for (const t of ["Да, ", "**Даниэль**", " настаивал", "[1]", "."]) turn.addToken(t);
  assert.ok(turn.hasText());
});

check("цитата рисует карточку", () => {
  turn.addCitation(citation);
  assert.equal(turn.node.findAll("moment").length, 1);
});

await sleep(220); // даём сработать отложенному рендеру

check("разметка собирается ПО ХОДУ печати, не только в конце", () => {
  const answer = turn.node.find("answer");
  assert.ok(answer.findTags("strong").length >= 1, "жирный не отрисовался во время печати");
  assert.ok(!answer.textContent.includes("**"), "звёздочки не должны просачиваться");
});

check("завершение: ссылка на цитату и кнопки оценки", () => {
  turn.finish();
  const answer = turn.node.find("answer");
  assert.ok(answer.findAll("ref").length === 1, "маркер [1] должен стать ссылкой");
  assert.ok(!answer.textContent.includes("[1]"), "сырой маркер остался в тексте");
  assert.equal(turn.node.findAll("ans-actions").length, 1);
});

check("обрыв сохраняет уже напечатанное", () => {
  const broken = createTurn("Вопрос", {});
  broken.addToken("Начало ответа");
  broken.fail("Связь оборвалась");
  assert.ok(broken.node.find("answer").textContent.includes("Начало ответа"), "ответ выбросили");
  assert.ok(broken.node.findAll("ans-actions").length === 1, "кнопки должны остаться");
});

check("восстановленный ход рисуется без ленты статусов", () => {
  const old = createTurn("Старый вопрос", { restored: true });
  old.restore({ answer: "## Итог\nТекст ответа [1].", citations: [citation] });
  assert.equal(old.node.findAll("trace").length, 0, "у восстановленного хода лента не нужна");
  assert.ok(old.node.find("answer").textContent.includes("Текст ответа"));
  assert.equal(old.node.findAll("moment").length, 1);
});

console.log("\nврезка с цитатой прямо в тексте:");

/** Готовый ход: ответ с двумя сносками и двумя цитатами. */
function turnWithSlot() {
  const t = createTurn("Вопрос", {});
  t.addCitation(citation);
  t.addCitation({ ...citation, n: 2, sec: 700, end: 760 });
  t.addToken("Первый абзац без ссылок.\n\nВторой абзац с фактом [1]. И ещё фраза [2].");
  t.finish();
  return t;
}

check("клик по сноске раздвигает текст ровно за её предложением", () => {
  const t = turnWithSlot();
  const refs = t.node.findAll("ref");
  refs[0]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[0] });

  const slot = t.node.find("slot");
  assert.ok(slot, "врезка не появилась");
  assert.ok(slot.find("moment"), "карточка не переехала во врезку");

  // врезка стоит ВНУТРИ абзаца, сразу за фразой со сноской — а не в конце
  const claim = t.node.findAll("claim").find((c) => c.findAll("ref").length);
  const paragraph = claim.parentElement;
  const kids = paragraph.kids.filter((k) => k.classList);
  assert.equal(kids.indexOf(slot), kids.indexOf(claim) + 1, "врезка не сразу после фразы");
  assert.ok(paragraph.textContent.includes("И ещё фраза"), "остаток абзаца должен остаться ниже");
});

check("во врезке нет блока «подпирает» — фраза и так подсвечена выше", () => {
  // повторять текст утверждения прямо под ним самим — показывать одно дважды
  const t = turnWithSlot();
  const refs = t.node.findAll("ref");
  refs[0]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[0] });
  const slot = t.node.find("slot");
  const claimBox = slot.find("m-claim");
  // блоки существуют (они нужны в нижнем списке), но во врезке скрыты стилями
  assert.ok(!claimBox || claimBox.hasAttribute("hidden"), "во врезке показан лишний текст");
  // «к [N] в ответе» здесь тоже ни к чему — мы и так стоим на этом месте
  const back = slot.find("m-back");
  assert.ok(back, "кнопка должна существовать (она нужна в списке)");
});

check("подсвечена та фраза, ради которой цитату открыли", () => {
  const t = turnWithSlot();
  const refs = t.node.findAll("ref");
  refs[0]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[0] });
  const lit = t.node.findAll("claim").filter((c) => c.classList.contains("claim-live"));
  assert.equal(lit.length, 1, "должна светиться ровно одна фраза");
  assert.ok(lit[0].textContent.includes("Второй абзац с фактом"), lit[0].textContent);
  assert.ok(!lit[0].textContent.includes("И ещё фраза"), "подсветка захватила соседнее предложение");
});

check("клик мимо врезки закрывает её и возвращает карточку в список", () => {
  const t = turnWithSlot();
  const refs = t.node.findAll("ref");
  refs[0]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[0] });
  const outside = new El("div");
  fireDoc("click", { target: outside });

  assert.equal(t.node.findAll("slot").length, 0, "врезка осталась открытой");
  assert.equal(t.node.find("mo-cards").findAll("moment").length, 2, "карточка не вернулась в список");
  assert.equal(t.node.findAll("claim").filter((c) => c.classList.contains("claim-live")).length, 0);
});

check("Esc тоже закрывает", () => {
  const t = turnWithSlot();
  const refs = t.node.findAll("ref");
  refs[0]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[0] });
  fireDoc("keydown", { key: "Escape" });
  assert.equal(t.node.findAll("slot").length, 0);
});

check("повторный клик по той же сноске сворачивает врезку", () => {
  const t = turnWithSlot();
  const refs = t.node.findAll("ref");
  const click = () => refs[0]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[0] });
  click();
  click();
  assert.equal(t.node.findAll("slot").length, 0);
});

check("клик по другой сноске переносит врезку туда", () => {
  const t = turnWithSlot();
  const refs = t.node.findAll("ref");
  refs[0]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[0] });
  refs[1]._on.click({ preventDefault() {}, stopPropagation() {}, target: refs[1] });
  const slots = t.node.findAll("slot");
  assert.equal(slots.length, 1, "врезка должна быть одна");
  const lit = t.node.findAll("claim").filter((c) => c.classList.contains("claim-live"));
  assert.equal(lit.length, 1);
  assert.ok(lit[0].textContent.includes("И ещё фраза"), lit[0].textContent);
});

console.log("\nход на странице записи:");

// ⚠️ Карточки строятся и на странице записи (владелец, 15.09): цитата понятна карточкой с
// плеером и подсветкой значимого, а не прыжком в расшифровку. Прежний режим `onCite` без
// карточек снят; конфликт двух владельцев одного `<video>` разводит читалка, не ход.
check("pruneUnreferenced оставляет только моменты со сноской в тексте", () => {
  const t = createTurn("Вопрос", { pruneUnreferenced: true });
  t.addCitation(citation);
  t.addCitation({ ...citation, n: 2, sec: 900 });
  t.addCitation({ ...citation, n: 3, sec: 1200 });
  t.addToken("Факт из записи [1] и ещё один [3].");
  t.finish();
  const cards = t.node.find("mo-cards").findAll("moment");
  assert.deepEqual(cards.map((c) => String(c.attrs["data-n"])), ["1", "3"], "неупомянутые карточки остались");
  assert.equal(t.node.findAll("mo-at").length, 2, "в сводке остались тайм-коды удалённых моментов");
  assert.equal(t.node.findAll("ref").length, 2, "сноски на месте");
});

check("без единой сноски в ответе обрезка ничего не трогает", () => {
  const t = createTurn("Вопрос", { pruneUnreferenced: true });
  t.addCitation(citation);
  t.addCitation({ ...citation, n: 2, sec: 900 });
  t.addToken("Ответ без ссылок.");
  t.finish();
  assert.equal(t.node.find("mo-cards").findAll("moment").length, 2, "найденное честнее показать, чем спрятать всё");
});

check("в чате (без pruneUnreferenced) остаются все моменты", () => {
  const t = createTurn("Вопрос", {});
  t.addCitation(citation);
  t.addCitation({ ...citation, n: 2, sec: 900 });
  t.addToken("Факт [1].");
  t.finish();
  assert.equal(t.node.find("mo-cards").findAll("moment").length, 2);
});

check("dispose снимает подписки хода на документе", () => {
  const before = (docHandlers.click || []).length + (docHandlers.keydown || []).length;
  const t = createTurn("Вопрос", {});
  const during = (docHandlers.click || []).length + (docHandlers.keydown || []).length;
  assert.equal(during, before + 2, "ход подписывается на клик мимо и Esc");
  t.dispose();
  const after = (docHandlers.click || []).length + (docHandlers.keydown || []).length;
  assert.equal(after, before, "после ухода со страницы слушатели копиться не должны");
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
