// Блок моментов под ответом: свёрнутая сводка и раскрытие по требованию.
//     node tests/js/moments.test.mjs
//
// Свёрнутое состояние — не просто «спрятали»: в нём человек должен увидеть,
// сколько нашлось, из каких записей и на каких минутах. Если сводка врёт или
// пустеет, ответ выглядит бездоказательным, а цитаты — потерянными.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    // Проигрыватель создаёт <video> сам (ui/player.js), поэтому заглушка элемента обязана
    // уметь то, что раньше умел глобальный Audio: перемотку, скорость и ручные события.
    if (this.tagName === "VIDEO") {
      this.paused = true;
      // метаданные считаем загруженными: иначе перемотка откладывается до события
      // loadedmetadata, и проверить, КУДА перематывают, невозможно
      this.readyState = 4;
      this.currentTime = 0;
      this.duration = 3600;
      this.playbackRate = 1;
      this.play = () => {
        this.paused = false;
        return Promise.resolve();
      };
      this.pause = () => {
        this.paused = true;
      };
      globalThis.__player = this; // тестам нужно двигать время и слать тики
    }
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
  // Списком, а не по одному на тип: плеер вешает на «play» и рассылку состояния,
  // и запуск покадрового цикла — при перезаписи второй молча съедал бы первый.
  addEventListener(type, fn) {
    ((this._on ||= {})[type] ||= []).push(fn);
  }
  // Расшифровка пересобирается (например, когда приехало утверждение ответа), и
  // слежение за прокруткой при этом снимает свой слушатель.
  removeEventListener(type, fn) {
    if (!this._on?.[type]) return;
    this._on[type] = fn ? this._on[type].filter((f) => f !== fn) : [];
  }
  /** Событие вручную: покадровый цикл плеера иначе превратился бы в бесконечную очередь. */
  fire(type, event = {}) {
    for (const fn of [...(this._on?.[type] || [])]) {
      fn({ target: this, preventDefault() {}, stopPropagation() {}, ...event });
    }
  }
  append(...kids) {
    for (const kid of kids.flat()) {
      if (kid == null) continue;
      if (kid instanceof El) {
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
  click() {
    this.fire("click");
  }
  closest(selector) {
    const cls = selector.replace(".", "");
    let node = this;
    while (node) {
      if (node.classList?.contains(cls)) return node;
      node = node.parentElement;
    }
    return null;
  }
  scrollIntoView() {}
  scrollBy(how) {
    (globalThis.__scrolls ||= []).push(how);
  }
  getBoundingClientRect() {
    return { width: 120, height: 20, top: 0, bottom: 20, left: 0 };
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

globalThis.document = {
  createElement: (t) => new El(t),
  createDocumentFragment: () => new El("#fragment"),
  createTextNode: (t) => String(t),
  body: new El("body"),
};
globalThis.matchMedia = () => ({ matches: false });
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
globalThis.getComputedStyle = () => ({ fontSize: "12px" });

const { momentsBlock } = await import(join(repo, "web/js/chat/moments-block.js"));

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

// Метка приходит от движка как «Заголовок · 12:34 · Спикеры», адрес медиа подставляет BFF
// (в шапке записи ссылки нет — там имя файла).
const cite = (n, rec, sec, end, title) => ({
  n,
  rec,
  sec,
  end,
  url: `/api/media/${rec}.mp4?slug=demo`,
  label: `${title} · ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")} · Мария Кузнецова`,
  text: "[20:34] [Мария Кузнецова] Текст фрагмента",
});

console.log("блок моментов:");

const block = momentsBlock({});
block.add(cite(1, "2026-07-17-spark", 1234, 1300, "Spark: партицирование и стадии"));
block.add(cite(2, "2026-07-17-spark", 2461, 2500, "Spark: партицирование и стадии"));
block.add(cite(3, "2026-07-03-flink", 1853, 1900, "Архивация через Flink"));

check("по умолчанию не раскрыт ни один момент", () => {
  // Видимость даёт CSS (`.moment:not(.open){display:none}`) — в заглушке стилей
  // нет, поэтому проверяем то, чем она управляется: класс раскрытия.
  const open = block.node.findAll("moment").filter((m) => m.classList.contains("open"));
  assert.equal(open.length, 0, "что-то раскрылось само — блок снова займёт пол-экрана");
  assert.equal(block.node.findAll("moment").length, 3, "карточки должны быть построены заранее");
});

check("сводка называет число моментов и записей", () => {
  const text = block.node.findAll("mo-head")[0].textContent;
  assert.ok(text.includes("3 момента"), text);
  assert.ok(text.includes("2 записей") || text.includes("2 записи"), text);
});

check("записи сгруппированы, а не перечислены по разу на цитату", () => {
  assert.equal(block.node.findAll("mo-grp").length, 2, "две цитаты одной записи должны слиться в строку");
  assert.equal(block.node.findAll("mo-at").length, 3, "тайм-код нужен каждой цитате");
});

check("в строке записи виден заголовок доклада и время", () => {
  // Заголовок, а не идентификатор: «2026-07-17-spark» человеку ничего не говорит.
  const row = block.node.findAll("mo-grp")[0].textContent;
  assert.ok(row.includes("Spark"), row);
  assert.ok(!row.includes("2026-07-17-spark"), row);
  assert.ok(row.includes("20:34"), row);
});

check("клик по тайм-коду раскрывает ровно его момент", () => {
  block.node.findAll("mo-at")[0].click();
  const open = block.node.findAll("moment").filter((m) => m.classList.contains("open"));
  assert.equal(open.length, 1, "раскрыться должен ровно один момент");
});

check("инлайн-ссылка [N] знает про свои цитаты", () => {
  assert.ok(block.has(1) && block.has(3));
  assert.ok(!block.has(99));
});

const single = momentsBlock({});
single.add(cite(1, "2026-07-17-spark", 60, 70, "тема"));
check("для одной записи не пишем «из 1 записи»", () => {
  const text = single.node.findAll("mo-head")[0].textContent;
  assert.ok(text.includes("1 момент"), text);
  assert.ok(!text.includes("из 1"), text);
});

// --- аккордеон: раскрыт ровно один момент ------------------------------------
// Иначе в списке оказывается несколько плееров сразу и непонятно, который звучит.

const acc = momentsBlock({});
acc.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема раз"));
acc.add(cite(2, "2026-07-03-flink", 1853, 1900, "тема два"));
const [cardA, cardB] = acc.node.findAll("moment");

check("раскрытие вторым тайм-кодом сворачивает первый", () => {
  acc.reveal(1);
  assert.ok(cardA.classList.contains("open"), "первый не раскрылся");
  acc.reveal(2);
  assert.ok(cardB.classList.contains("open"), "второй не раскрылся");
  assert.ok(!cardA.classList.contains("open"), "первый остался раскрытым — плееров стало два");
});

check("клик по самой карточке тоже сворачивает соседей", () => {
  acc.reveal(1);
  cardB.click();
  assert.ok(cardB.classList.contains("open"));
  assert.ok(!cardA.classList.contains("open"), "клик мимо списка не свернул прежний момент");
});

// --- самый плотный вид: строка бейджей ---------------------------------------

check("по умолчанию — плотный вид", () => {
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  assert.ok(fresh.node.classList.contains("mo-compact"), "блок раскрыт сразу — ответ снова оттеснён");
});

check("клик по бейджам разворачивает, а не только по шапке", () => {
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  fresh.node.findAll("mo-badges")[0].click();
  assert.ok(!fresh.node.classList.contains("mo-compact"), "бейджи не развернули блок");
});

check("бейдж на запись, а не на цитату", () => {
  // Три цитаты из двух записей: бейджей должно быть два.
  assert.equal(block.node.findAll("mo-badge").length, 2);
  assert.ok(block.node.findAll("mo-badge")[0].textContent.includes("Spark"));
});

check("шапка разворачивает сводку и схлопывает обратно", () => {
  // Свой блок: соседние проверки уже переключали состояние общего, и зависеть
  // от порядка тестов — верный способ однажды долго искать несуществующий баг.
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  const head = fresh.node.findAll("mo-head")[0];
  head.click();
  assert.ok(!fresh.node.classList.contains("mo-compact"), "не развернулось");
  head.click();
  assert.ok(fresh.node.classList.contains("mo-compact"), "не схлопнулось обратно");
});

check("схлопывание закрывает раскрытый момент", () => {
  block.reveal(1); // reveal сам разворачивает блок
  block.node.findAll("mo-head")[0].click();
  const open = block.node.findAll("moment").filter((m) => m.classList.contains("open"));
  assert.equal(open.length, 0, "карточка осталась висеть под строкой бейджей");
  block.node.findAll("mo-head")[0].click(); // возвращаем как было
});

console.log("\nдорога назад к сноске:");

check("пришли по [N] — карточка предлагает вернуться к тому месту ответа", () => {
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  const back = fresh.node.find("m-back");
  assert.ok(back.hasAttribute("hidden"), "без сноски возвращаться некуда");

  const footnote = new El("a"); // та самая цифра [1] в тексте ответа
  footnote.isConnected = true;
  let shown = null;
  footnote.scrollIntoView = () => (shown = footnote);
  fresh.reveal(1, { from: footnote });

  assert.ok(!back.hasAttribute("hidden"), "кнопка возврата не появилась");
  assert.ok(back.textContent.includes("[1]"), back.textContent);
  back.fire("click", { stopPropagation() {}, preventDefault() {}, target: back });
  assert.equal(shown, footnote, "не вернулись к сноске");
  assert.ok(footnote.classList.contains("ref-flash"), "сноску надо подсветить: на экране целый абзац");
});

check("момент, открытый не из текста, кнопкой возврата не дразнит", () => {
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  fresh.reveal(1); // тайм-код из сводки — пришли не из ответа
  assert.ok(fresh.node.find("m-back").hasAttribute("hidden"));
});

check("дорога в текст остаётся и после сворачивания карточки", () => {
  // метка в ответе никуда не делась — значит и прыгнуть к ней можно всегда
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  const footnote = new El("a");
  footnote.isConnected = true;
  footnote.scrollIntoView = () => {};
  fresh.reveal(1, { from: footnote });
  fresh.collapseAll();
  assert.ok(!fresh.node.find("m-back").hasAttribute("hidden"));
});

check("кнопка есть у любой карточки, чья сноска стоит в ответе", () => {
  // в нижнем списке дорога в текст нужна ровно так же, как во врезке
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  fresh.add(cite(2, "2026-07-03-flink", 1853, 1900, "другая тема"));
  const footnote = new El("a");
  footnote.isConnected = true;
  assert.ok(fresh.node.findAll("m-back").every((b) => b.hasAttribute("hidden")), "до ответа кнопок быть не должно");

  fresh.setClaims(new Map([[2, ["Фраза с цитатой [2]."]]]), new Map([[2, footnote]]));
  const [first, second] = fresh.node.findAll("m-back");
  assert.ok(first.hasAttribute("hidden"), "у цитаты без сноски прыгать некуда");
  assert.ok(!second.hasAttribute("hidden"), "у процитированной кнопка обязана появиться");
  assert.ok(second.textContent.includes("[2]"), second.textContent);
});

check("ссылка [N] из текста разворачивает схлопнутое", () => {
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 1234, 1300, "тема"));
  fresh.add(cite(2, "2026-07-03-flink", 1853, 1900, "тема два"));
  assert.ok(fresh.node.classList.contains("mo-compact"), "новый блок обязан быть плотным");
  fresh.reveal(2);
  assert.ok(!fresh.node.classList.contains("mo-compact"), "цитата из текста не развернула блок");
});

// --- важные места: волна и воспроизведение ---------------------------------
//
// Карточка тянет слова с сервера, поэтому здесь подменяем ответ `/words`:
// одна реплика на минуту, в середине которой сказано ключевое слово.
console.log("\nважные места в карточке:");

const SPOKEN = [];
for (let i = 0; i < 60; i++) SPOKEN.push([i === 30 ? "видеокарта" : `слово${i}`, 100 + i, 100.8 + i]);
// Контекст вокруг цитаты — с тем же ключевым словом: на нём и ловится баг,
// когда важные места начинают считаться по разговору, а не по самой цитате.
const BEFORE = [];
for (let i = 0; i < 30; i++) BEFORE.push([i === 20 ? "видеокарта" : `до${i}`, 60 + i, 60.8 + i]);
const AFTER = [];
for (let i = 0; i < 30; i++) AFTER.push([i === 10 ? "видеокарта" : `после${i}`, 161 + i, 161.8 + i]);

globalThis.fetch = async (path) => {
  const wide = /pad=[1-9]/.test(String(path));
  const cite = { start: 100, end: 160, speaker: "Валентин Малых", words: SPOKEN };
  return {
    ok: true,
    json: async () => ({
      record: "2026-08-24-credit-limit",
      aligned: true,
      turns: wide
        ? [
            { start: 60, end: 99, speaker: "Даниэль Щебентовский", words: BEFORE },
            cite,
            { start: 161, end: 200, speaker: "Даниэль Щебентовский", words: AFTER },
          ]
        : [cite],
    }),
  };
};

const player = await import(join(repo, "web/js/ui/player.js"));
const { hotSpans } = await import(join(repo, "web/js/chat/gist.js"));
const hot = hotSpans([{ start: 100, end: 160, words: SPOKEN }], ["видеокарт"]).spans;

const hotBlock = momentsBlock({});
hotBlock.add({
  ...cite(1, "2026-08-24-credit-limit", 100, 160, "Кредитный лимит"),
  keywords: ["видеокарт"],
  found_by: [
    { query: "H200 видеокарты обучение больших моделей", tool: "search", step: 1, rank: 2 },
    { query: "H200 обучение моделей", tool: "get_doc", step: 4, rank: 3 },
  ],
});
hotBlock.reveal(1);
await new Promise((resolve) => setTimeout(resolve, 20)); // ждём слова и пересборку

const card = hotBlock.node.findAll("moment")[0];
const wave = card.find("wave");

check("видно, чем цитата подпирает ответ, и клик ведёт в то место", () => {
  // самое честное объяснение «зачем она здесь»: не ранжирование и не совпадение
  // слов, а место, ради подкрепления которого агент её и привёл
  const footnote = new El("a");
  footnote.isConnected = true;
  let shown = null;
  footnote.scrollIntoView = () => (shown = footnote);
  hotBlock.setClaims(new Map([[1, ["Малых оценил объём в 640 ГБ [1]."]]]), new Map([[1, footnote]]));

  const claim = card.find("m-claim");
  assert.ok(!claim.hasAttribute("hidden"), "блок не показан");
  assert.ok(claim.textContent.includes("640 ГБ"), claim.textContent);
  claim.onclick({ stopPropagation() {}, preventDefault() {} });
  assert.equal(shown, footnote, "клик по утверждению не вернул в текст ответа");
});

check("цитата, на которую в ответе не сослались, блоком не притворяется", () => {
  // таких большинство: на живом ответе 15 из 17 цитат в тексте не упомянуты
  hotBlock.setClaims(new Map(), new Map());
  assert.ok(card.find("m-claim").hasAttribute("hidden"));
});

check("подпись и протокол поиска — под текстом, а не над ним", () => {
  // над расшифровкой читают, а не разбираются в служебных подробностях
  const kids = card.find("tx").kids.filter((k) => k.classList);
  const at = (cls) => kids.findIndex((k) => k.classList.contains(cls));
  assert.ok(at("kar") >= 0 && at("tx-foot") > at("kar"), "служебная строка осталась над текстом");
  const foot = card.find("tx-foot");
  assert.ok(foot.find("tx-cap"), "подпись «расшифровка фрагмента» потерялась");
  assert.ok(foot.find("m-prov"), "протокол поиска потерялся");
});

check("протокол поиска свёрнут по умолчанию", () => {
  // это служебные подробности, а не то, ради чего открыли цитату
  const prov = card.find("m-prov");
  assert.ok(!prov.hasAttribute("open"), "блок раскрыт и занимает место зря");
  assert.ok(prov.find("m-prov-sum").textContent.includes("поиск по базе"), "в свёрнутой строке не видно, чем нашли");
});

check("видно, каким запросом и как фрагмент нашёлся", () => {
  // единственный честный ответ на «почему это здесь»: подсветка слов говорит
  // лишь о совпадении корней, а это — настоящий запрос агента
  const prov = card.find("m-prov").textContent;
  assert.ok(prov.includes("H200 видеокарты"), prov);
  assert.ok(prov.includes("поиск по базе"), prov);
  assert.ok(prov.includes("2-й в выдаче"), prov);
  assert.ok(prov.includes("находилось 2 раза"), "повторная находка — сильный сигнал");
});

check("важное место видно прямо на волне", () => {
  const lit = card.findAll("bar").filter((b) => b.classList.contains("hot"));
  assert.ok(lit.length > 0, "волна не подсвечена");
  assert.ok(lit.length < card.findAll("bar").length, "подсвечено всё — это не подсказка");
});

check("запись грузится без «#t=» — иначе браузер сам уводит в начало чанка", () => {
  // Первый клик по слову играл с начала чанка, повторный — верно: media
  // fragment из ссылки на момент отрабатывался при загрузке метаданных и
  // затирал нашу перемотку, которая ждёт того же события.
  assert.ok(!String(globalThis.__player.src).includes("#"), globalThis.__player.src);
});

check("перемотка ждёт метаданных, если запись ещё не готова", () => {
  globalThis.__player.readyState = 0;
  const word = card.findAll("kar-w").find((w) => w.textContent === "слово40");
  word.fire("click", { stopPropagation() {}, preventDefault() {}, target: word });
  globalThis.__player.readyState = 4;
  globalThis.__player.fire("loadedmetadata");
  assert.equal(Math.round(player.state().time), 140, "перемотка потерялась при загрузке");
});

check("клик по волне рядом с важным местом ведёт в его начало", () => {
  wave.getBoundingClientRect = () => ({ left: 0, width: 100 });
  // 0.5 ширины = 130-я секунда, ровно то место, где сказано ключевое слово
  wave.fire("click", { clientX: 50, stopPropagation() {}, preventDefault() {}, target: wave });
  assert.equal(Math.round(player.state().time), Math.round(hot[0].start));
});

// Окно чтения: у заглушки прямоугольники нулевые, а слежение считает видимость
// именно по ним — без этого «слово за экраном» неотличимо от видимого.
card.find("tx").getBoundingClientRect = () => ({ top: 0, height: 200, width: 600, bottom: 200, left: 0 });
const scrolls = (globalThis.__scrolls ||= []);

check("перемотка волной подводит текст к нужному слову", () => {
  // Звук уезжал в середину фрагмента, а расшифровка оставалась на месте:
  // слушаешь одно, читаешь другое, и место приходится искать руками.
  const jumps = [];
  for (const w of card.findAll("kar-w")) {
    // слово «за экраном»: слежение считает видимость по рамке элемента
    w.getBoundingClientRect = () => ({ top: 5000, bottom: 5020, height: 20 });
  }
  const before = scrolls.length;
  wave.getBoundingClientRect = () => ({ left: 0, width: 100, top: 0, height: 20 });
  wave.fire("click", { clientX: 70, stopPropagation() {}, preventDefault() {}, target: wave });
  jumps.push(scrolls.length - before);
  assert.ok(jumps[0] > 0, "текст не подтянулся к месту, откуда пошёл звук");
});

check("клик по слову, которое и так видно, страницу не дёргает", () => {
  for (const w of card.findAll("kar-w")) {
    w.getBoundingClientRect = () => ({ top: 40, bottom: 60, height: 20 });
  }
  const before = scrolls.length;
  const word = card.findAll("kar-w")[5];
  word.fire("click", { stopPropagation() {}, preventDefault() {}, target: word });
  assert.equal(scrolls.length, before, "прокрутка сработала зря");
});

check("клик по пустому месту волны перематывает туда, куда ткнули", () => {
  wave.fire("click", { clientX: 2, stopPropagation() {}, preventDefault() {}, target: wave });
  assert.ok(Math.abs(player.state().time - 101) < 2, `попали в ${player.state().time}`);
});

player.pause(); // играет с прошлой проверки, иначе «играть» сработает как пауза
card.find("play").fire("click", { stopPropagation() {}, preventDefault() {}, target: card });

check("«играть» запускает звук прямо в обработчике клика", () => {
  // ⚠️ Стартовать с первого важного места пробовали и откатили: за тайм-кодами
  // нужен await, а он уводит play() из жеста — браузер блокирует такой звук.
  // Поэтому здесь никаких ожиданий: звук пошёл сразу, с начала фрагмента.
  assert.equal(Math.round(player.state().time), 100);
  assert.ok(player.state().playing);
});

check("повторное нажатие ставит на паузу, а не прыгает заново", () => {
  card.find("play").fire("click", { stopPropagation() {}, preventDefault() {}, target: card });
  assert.ok(!player.state().playing);
});

check("«только важное» ведёт в первое важное место", () => {
  card.findAll("m-tool-key")[0].fire("click", { stopPropagation() {}, preventDefault() {}, target: card });
  assert.equal(Math.round(player.state().time), Math.round(hot[0].start));
});

check("действия сверху, подпись записи и плеер — внизу", () => {
  // «только важное» предлагает не читать фрагмент целиком, поэтому стоит ДО
  // текста; а доклад, спикеры и тайм-код нужны, когда собираешься смотреть.
  const order = card.kids.filter((k) => k.classList).map((k) => [...k.classList._set][0]);
  const at = (cls) => order.indexOf(cls);
  assert.ok(at("m-tools") < at("tx"), "кнопки оказались под текстом");
  assert.ok(at("tx") < at("m-head"), "подпись записи должна быть под расшифровкой");
  assert.ok(at("m-head") < at("m-foot"), "волна должна быть в самом низу");
  // ссылка и «в читалку» переехали в ту же строку
  assert.ok(card.find("m-tools").find("m-share"), "ссылки на момент нет в строке действий");
  assert.ok(card.find("m-tools").find("m-open"), "«в читалку» нет в строке действий");
});

const placesBefore = card.find("m-tool-key").textContent;
card.findAll("m-tool").find((b) => b.textContent === "шире").click();
await new Promise((resolve) => setTimeout(resolve, 20));

check("«шире» не выдумывает важных мест в контексте", () => {
  // Ловили на живых цитатах: ключевые слова из соседних реплик рождали места за
  // границей чанка. На волне их нет (она в масштабе цитаты), а перемотку туда
  // карточка сама же гасит на конце чанка — «мест три, а работают два».
  assert.equal(card.find("m-tool-key").textContent, placesBefore, "число мест изменилось от контекста");
  const strayed = card
    .findAll("kar-hot")
    .filter((box) => /до\d|после\d/.test(box.textContent));
  assert.deepEqual(strayed, [], "выделено место за пределами цитаты");
});

check("слово из контекста играет оттуда, а не сбрасывается в начало цитаты", () => {
  // Ловили руками: карточка глушит звук на конце цитаты, а слово в подтянутом
  // контексте лежит за этой границей — воспроизведение прыгало в начало чанка
  // через мгновение после клика.
  const word = card
    .findAll("kar-w")
    .find((w) => w.textContent === "после11"); // 172-я секунда, уже за концом цитаты
  word.fire("click", { stopPropagation() {}, preventDefault() {}, target: word });
  assert.ok(player.state().time > 160, "клик не сработал");
  globalThis.__player.currentTime = 175; // прошло три секунды звука
  globalThis.__player.fire("timeupdate");
  assert.ok(player.state().playing, "карточка сама остановила чужой контекст");
  assert.ok(player.state().time > 160, `звук уехал в ${player.state().time}`);
});

check("за концом показанного текста карточка всё же останавливается", () => {
  globalThis.__player.currentTime = 260; // дальше не показано ничего
  globalThis.__player.fire("timeupdate");
  assert.ok(!player.state().playing, "иначе воспроизведение поедет по всей записи");
});

console.log("\nуход хода и обрезка сводки:");

check("dispose карточки снимает подписку на плеер: тики звука её больше не трогают", () => {
  // ⚠️ Раньше подписка снималась по событию «remove», которого у DOM нет, — то есть никогда:
  // каждая карточка каждого хода слушала плеер до перезагрузки страницы.
  player.pause();
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 100, 160, "Spark: партицирование и стадии"));
  const card = fresh.node.findAll("moment")[0];
  card.find("play").fire("click", { stopPropagation() {}, preventDefault() {}, target: card });
  globalThis.__player.currentTime = 130;
  globalThis.__player.fire("timeupdate");
  const tc = card.find("tc");
  const shown = tc.textContent;
  assert.equal(shown, "2:10", "пока подписка жива, тайм-код идёт за звуком");
  fresh.dispose();
  globalThis.__player.currentTime = 150;
  globalThis.__player.fire("timeupdate");
  assert.equal(tc.textContent, shown, "после dispose карточка всё ещё слушает плеер");
  player.pause();
});

check("keepOnly оставляет названные моменты и пересобирает сводку по оставшимся", () => {
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 100, 160, "Spark: партицирование и стадии"));
  fresh.add(cite(2, "2026-07-17-spark", 200, 260, "Spark: партицирование и стадии"));
  fresh.add(cite(3, "2026-07-03-flink", 300, 360, "Архивация через Flink"));
  fresh.keepOnly([2]);
  assert.equal(fresh.node.findAll("moment").length, 1, "лишние карточки должны уйти из DOM");
  assert.ok(!fresh.has(1) && fresh.has(2) && !fresh.has(3));
  assert.equal(fresh.node.findAll("mo-grp").length, 1, "строка записи без моментов должна уйти");
  assert.equal(fresh.node.findAll("mo-at").length, 1, "в сводке остался тайм-код удалённого момента");
  assert.equal(fresh.node.findAll("mo-badge").length, 1);
  assert.ok(fresh.node.findAll("mo-head")[0].textContent.includes("1 момент"), "счётчик не пересчитан");
});

check("keepOnly без номеров ничего не трогает: найденное честнее показать", () => {
  const fresh = momentsBlock({});
  fresh.add(cite(1, "2026-07-17-spark", 100, 160, "Spark: партицирование и стадии"));
  fresh.add(cite(2, "2026-07-03-flink", 300, 360, "Архивация через Flink"));
  fresh.keepOnly([]);
  assert.equal(fresh.node.findAll("moment").length, 2);
  assert.equal(fresh.node.findAll("mo-grp").length, 2);
});

console.log(failures ? `\nпровалов: ${failures}` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
