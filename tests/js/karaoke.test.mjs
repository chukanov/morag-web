// Караоке без браузера: подсветка попадает на звучащее слово, клик перематывает.
//     node tests/js/karaoke.test.mjs
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
    this.parentElement = null;
    this.classList = {
      _s: new Set(),
      add: (c) => this.classList._s.add(c),
      remove: (c) => this.classList._s.delete(c),
      contains: (c) => this.classList._s.has(c),
      toggle: (c, on) => (on ? this.classList._s.add(c) : this.classList._s.delete(c)),
    };
  }
  set className(v) {
    for (const c of String(v).split(/\s+/).filter(Boolean)) this.classList._s.add(c);
  }
  set textContent(v) {
    this.kids = [String(v)];
  }
  get textContent() {
    return this.kids.map((k) => (typeof k === "string" ? k : k.textContent)).join("");
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
  replaceWith(node) {
    const parent = this.parentElement;
    if (!parent) return;
    parent.kids = parent.kids.map((k) => (k === this ? node : k));
    node.parentElement = parent;
    this.parentElement = null;
  }
  replaceChildren(...kids) {
    this.kids = [];
    this.append(...kids);
  }
  focus() {}
  click() {
    this._on?.click?.({ stopPropagation() {} });
  }
  prepend(kid) {
    this.kids.unshift(kid);
  }
  remove() {}
  querySelector(sel) {
    const want = String(sel).replace(/^\./, "");
    for (const kid of this.kids) {
      if (!(kid instanceof El)) continue;
      if (kid.classList.contains(want)) return kid;
      const deeper = kid.querySelector(sel);
      if (deeper) return deeper;
    }
    return null;
  }
}

globalThis.document = {
  createElement: (t) => new El(t),
  createDocumentFragment: () => new El("#f"),
  createTextNode: (t) => String(t),
  body: new El("body"),
};
globalThis.matchMedia = () => ({ matches: false });

// прокрутка страницы: запоминаем, куда нас двигали и как
globalThis.innerHeight = 800;
const scrolls = [];
globalThis.scrollBy = (opts) => scrolls.push(opts);
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};

const { buildKaraoke, follower } = await import(join(repo, "web/js/ui/karaoke.js"));

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

const TURNS = [
  { start: 0, end: 12, speaker: "Первый", words: [["раз", 0, 1], ["два", 3, 4], ["три", 8, 9]] },
  { start: 12, end: 30, speaker: "Второй", words: [["четыре", 12, 13], ["пять", 20, 21]] },
];

// ⚠️ Дети `.kar` — это и абзацы, и шапки с именами: шапка стала СОСЕДОМ абзацев, чтобы имя
// могло висеть, пока идут реплики этого человека. Поэтому абзацы всегда выбираем по классу,
// а не по порядку — на этом тесты один раз уже поехали.
const turnsOf = (k) => k.node.kids.filter((x) => x instanceof El && x.classList.contains("kar-turn"));
const headsOf = (k) => k.node.kids.filter((x) => x instanceof El && x.classList.contains("kar-who"));
const words = (k) => turnsOf(k).flatMap((turn) => turn.kids.filter((x) => x instanceof El && x.classList.contains("kar-text")))
  .flatMap((p) => p.kids.filter((x) => x instanceof El));

console.log("подсветка:");
const seeks = [];
const kar = buildKaraoke(TURNS, { onSeek: (sec) => seeks.push(sec) });
const all = words(kar);

check("построены все слова обеих реплик", () => {
  assert.equal(kar.words, 5);
  assert.deepEqual(all.map((w) => w.textContent), ["раз", "два", "три", "четыре", "пять"]);
});

check("горит слово, которое звучит сейчас", () => {
  kar.at(3.5);
  assert.ok(all[1].classList.contains("on"), "должно гореть «два»");
  assert.ok(!all[0].classList.contains("on"));
});

check("слово держится в паузе до следующего, а не мигает", () => {
  // между словами десятки миллисекунд тишины; без выдержки подсветка моргала бы
  kar.at(4.2);
  assert.ok(all[1].classList.contains("on"), "«два» погасло сразу после конца");
});

check("в длинной паузе не горит ничего", () => {
  kar.at(6.0);
  assert.ok(!all.some((w) => w.classList.contains("on")), "в тишине подсвечено слово");
});

check("прочитанное приглушается", () => {
  kar.at(8.5);
  assert.ok(all[0].classList.contains("done") && all[1].classList.contains("done"));
  assert.ok(!all[2].classList.contains("done"), "текущее слово не «прочитано»");
});

check("подсветка переходит через границу реплик", () => {
  kar.at(12.5);
  assert.ok(all[3].classList.contains("on"), "должно гореть «четыре» из второй реплики");
});

check("перемотка назад снимает «прочитано»", () => {
  // без этого при откате назад весь текст оставался бы серым
  kar.at(0.5);
  assert.ok(all[0].classList.contains("on"));
  assert.ok(!all[3].classList.contains("done"), "«четыре» осталось прочитанным после отката");
});

check("звучащая реплика подсвечена целиком", () => {
  // одно горящее слово теряется в полотне текста — абзац помогает не потерять строку
  kar.at(3.5);
  const turns = turnsOf(kar);
  assert.ok(turns[0].classList.contains("live"), "первая реплика не отмечена звучащей");
  assert.ok(!turns[1].classList.contains("live"));
  kar.at(12.5);
  assert.ok(!turns[0].classList.contains("live"), "прошлая реплика осталась отмеченной");
  assert.ok(turns[1].classList.contains("live"));
});

check("реплика НЕ гаснет в паузе между словами", () => {
  // подсветка абзаца шла от текущего слова, а слово в паузе гаснет — абзац моргал
  const turns = turnsOf(kar);
  kar.at(3.5);                       // звучит «два»
  assert.ok(turns[0].classList.contains("live"));
  kar.at(6.0);                       // пауза: слова нет...
  assert.equal(kar.current, null, "в паузе не должно гореть слово");
  assert.ok(turns[0].classList.contains("live"), "абзац погас в паузе — это и есть моргание");
  kar.at(8.5);                       // ...и снова слово той же реплики
  assert.ok(turns[0].classList.contains("live"));
});

check("между репликами подсветка переходит, а не остаётся на обеих", () => {
  const turns = turnsOf(kar);
  kar.at(12.5);
  assert.ok(turns[1].classList.contains("live"));
  assert.ok(!turns[0].classList.contains("live"));
});

check("после конца последней реплики подсветка снимается", () => {
  const turns = turnsOf(kar);
  kar.at(45);
  assert.ok(!turns.some((t) => t.classList.contains("live")), "выпуск кончился, а абзац горит");
});

check("текущее слово можно спросить, а не только поймать на смене", () => {
  // at() отдаёт слово лишь при СМЕНЕ; наводка по «играть» без этого не сработает
  kar.at(3.5);
  assert.equal(kar.at(3.6), null, "at() отдал слово повторно");
  assert.equal(kar.current?.node.textContent, "два");
});

console.log("\nкуда вести взгляд:");
check("в паузе слово всё равно находится — по времени, а не по подсветке", () => {
  // на этом ломался возврат «к звучащему месту»: подсвеченного слова нет,
  // и прыжок уходил к началу абзаца вместо самой речи
  kar.at(6.0);
  assert.equal(kar.current, null, "в паузе не должно быть подсвеченного слова");
  assert.equal(kar.wordAt(6.0)?.node.textContent, "три", "не нашли ближайшее слово");
});

check("внутри слова берётся оно само", () => {
  assert.equal(kar.wordAt(3.5)?.node.textContent, "два");
});

check("до первого слова ведём к первому", () => {
  assert.equal(kar.wordAt(-5)?.node.textContent, "раз");
});

check("после последнего слова остаёмся на нём", () => {
  assert.equal(kar.wordAt(999)?.node.textContent, "пять");
});

check("на пустом выпуске слова нет и ничего не падает", () => {
  assert.equal(buildKaraoke([], {}).wordAt(10), null);
});

console.log("");
check("clear гасит подсветку", () => {
  kar.at(3.5);
  kar.clear();
  assert.ok(!all.some((w) => w.classList.contains("on")));
});

console.log("\nперемотка:");
check("клик по слову перематывает на его начало", () => {
  all[4]._on.click({ stopPropagation() {} });
  assert.deepEqual(seeks.at(-1), 20, "перемотали не на начало «пять»");
});

check("клик по тайм-коду реплики перематывает на её начало", () => {
  // Время теперь ДЕТИЩЕ самой реплики, а не шапки: оно стоит слева от первой строки, а не над ней.
  const tc = turnsOf(kar)[1].kids.find((x) => x instanceof El && x.classList.contains("kar-tc"));
  tc._on.click({ stopPropagation() {} });
  assert.equal(seeks.at(-1), 12);
});

check("продолжение речи идёт БЕЗ шапки", () => {
  // Ради этого перестановка и делалась: пустая строка над каждым абзацем растягивала
  // расшифровку и резала глаз. Имя нужно там, где говорящий СМЕНИЛСЯ.
  const подряд = buildKaraoke([
    { start: 0, end: 5, speaker: "Первый", words: [["раз", 0, 1]] },
    { start: 5, end: 9, speaker: "Первый", words: [["два", 5, 6]] },
    { start: 9, end: 12, speaker: "Второй", words: [["три", 9, 10]] },
  ], {});
  assert.equal(headsOf(подряд).length, 2, "шапка обязана быть только при смене говорящего");
  const порядок = подряд.node.kids
    .filter((x) => x instanceof El)
    .map((x) => (x.classList.contains("kar-who") ? "имя" : "абзац"));
  assert.deepEqual(порядок, ["имя", "абзац", "абзац", "имя", "абзац"],
    "имя обязано стоять ПЕРЕД своими абзацами и быть их соседом, иначе оно не сможет висеть");
  const времён = turnsOf(подряд).map((t) =>
    t.kids.filter((x) => x instanceof El && x.classList.contains("kar-tc")).length);
  assert.deepEqual(времён, [1, 1, 1], "время нужно у КАЖДОГО абзаца: это «слушать отсюда»");
});

console.log("\nпустые данные:");
check("выпуск без слов не роняет сборку", () => {
  const empty = buildKaraoke([], {});
  assert.equal(empty.words, 0);
  assert.equal(empty.at(5), null);
});

console.log("\nвыделенные места (в карточке-моменте):");

// В карточке фрагмент виден целиком, но важные места выделены. Здесь важным
// объявлено только «три» (8–9 с).
//
// ⚠️ Прятать «воду» между местами пробовали и отказались (2026-08-16): текст из
// обрывков с многоточиями читается хуже целого. Тесты на сжатие удалены вместе
// с ним — если идея вернётся, начинать надо с того, что она уже была отменена.
const HOT = [{ start: 8, end: 9 }];
// Горячие слова лежат внутри обёртки куска, а между группами стоят многоточия —
// поэтому собираем слова вглубь и по классу, а не плоским списком детей абзаца.
const deep = (node, cls, out = []) => {
  for (const kid of node.kids || []) {
    if (typeof kid === "string") continue;
    if (kid.classList?.contains(cls)) out.push(kid);
    deep(kid, cls, out);
  }
  return out;
};
const spoken = (k) => deep(k.node, "kar-w");

check("выделено только важное место, текст вокруг не тронут", () => {
  // ⚠️ приглушать «воду» пробовали и вернули: бледный дальний план съедает
  // контраст расшифровки, а выделение на обычном тексте видно даже лучше
  const k = buildKaraoke(TURNS, { spans: HOT });
  const inHot = spoken(k).map((w) => Boolean(deep(k.node, "kar-hot").find((b) => b.kids.includes(w))));
  assert.deepEqual(inHot, [false, false, true, false, false]);
  assert.equal(spoken(k).length, 5, "фрагмент показывается целиком");
});

check("ключевое слово подчёркнуто, обычное — нет", () => {
  // движок присылает ту форму, что стояла в его чанке, — сходство по корню
  const k = buildKaraoke(TURNS, { spans: HOT, keywords: ["трижды"] });
  const w = spoken(k);
  assert.ok(w[2].classList.contains("kar-key"), "«три» совпадает с «трижды» по корню");
  assert.ok(!w[0].classList.contains("kar-key"));
});

check("выделенное место — цельный элемент, его видно и можно подсветить", () => {
  const k = buildKaraoke(TURNS, { spans: HOT });
  const boxes = deep(k.node, "kar-hot");
  assert.equal(boxes.length, 1);
  assert.equal(boxes[0].textContent.trim(), "три");
  k.setActiveSpan(0);
  assert.ok(boxes[0].classList.contains("on-air"), "звучащий кусок должен отмечаться");
  k.setActiveSpan(-1);
  assert.ok(!boxes[0].classList.contains("on-air"));
});

check("первое выделение отдаётся наружу — к нему ведут взгляд", () => {
  // карточка открывается не на начале чанка: он вырезан из середины разговора
  const k = buildKaraoke(TURNS, { spans: HOT });
  assert.equal(k.firstHot, deep(k.node, "kar-hot")[0]);
  assert.equal(k.hotCount, 1);
});

check("без выделенных мест текст остаётся обычным", () => {
  const k = buildKaraoke(TURNS, {});
  assert.equal(deep(k.node, "kar-hot").length, 0);
  assert.equal(k.firstHot, null);
});

console.log("\nслежение за словом (страница крутится целиком):");

// слово «висит» в 700px от верха вьюпорта — глубоко ниже центра чтения
const far = { node: { getBoundingClientRect: () => ({ top: 700, height: 20 }) } };
const near = { node: { getBoundingClientRect: () => ({ top: 480, height: 20 }) } };

check("центр чтения считается НИЖЕ липких шапки и плеера", () => {
  scrolls.length = 0;
  follower(null, { headroom: 130 }).follow(far);
  // без учёта липкого центр был бы на 400; с ним — на 130 + (800-130)/2 = 465
  assert.equal(scrolls.length, 1, "прокрутки не случилось");
  assert.ok(Math.abs(scrolls[0].top - (700 + 10 - 465)) < 1, `сдвинули на ${scrolls[0].top}`);
});

check("слово рядом с центром прокрутку НЕ дёргает", () => {
  scrolls.length = 0;
  follower(null, { headroom: 130 }).follow(near);
  assert.equal(scrolls.length, 0, "страница дёрнулась из-за мелкого отклонения");
});

check("переход к моменту прыгает сразу, без плавности", () => {
  // плавно ехать через часовой выпуск — это секунды ожидания на пустом месте
  scrolls.length = 0;
  follower(null, { headroom: 130 }).jump(far);
  assert.equal(scrolls.length, 1);
  assert.equal(scrolls[0].behavior, "auto");
});

check("слежение плавное, а не рывком", () => {
  scrolls.length = 0;
  follower(null, { headroom: 0 }).follow(far);
  assert.equal(scrolls[0].behavior, "smooth");
});

console.log("\nвозврат внимания:");
const offscreen = { node: { getBoundingClientRect: () => ({ top: 1900, bottom: 1920, height: 20 }) } };
const onscreen = { node: { getBoundingClientRect: () => ({ top: 400, bottom: 420, height: 20 }) } };

check("слово за пределами экрана считается потерянным", () => {
  const t = follower(null, { headroom: 130 });
  assert.equal(t.lost(offscreen), true);
  assert.equal(t.lost(onscreen), false);
});

check("слово под липким плеером — тоже потеряно", () => {
  // формально на экране, но закрыто плеером: подсвечено невидимое место
  const under = { node: { getBoundingClientRect: () => ({ top: 40, bottom: 60, height: 20 }) } };
  assert.equal(follower(null, { headroom: 130 }).lost(under), true);
});

check("после прокрутки человеком слежение молчит, а прыжок — работает", () => {
  const t = follower(null, { headroom: 0 });
  t.jump(far);          // наш прыжок не должен считаться «человек крутит»
  scrolls.length = 0;
  t.follow(far);
  assert.equal(scrolls.length, 1, "после нашего же прыжка слежение замолчало");
});

console.log("\nзамок на время правки (карточка голоса, поле правки):");

check("под замком слежение стоит, прыжок по ссылке — работает; снятие — ровно один раз", () => {
  const t = follower(null, { headroom: 0 });
  const release = t.lock();
  scrolls.length = 0;
  t.follow(far);
  assert.equal(scrolls.length, 0, "страница уехала из-под карточки");
  assert.equal(t.locked, true);
  t.jump(far);
  assert.equal(scrolls.length, 1, "переход по ссылке под замком обязан работать");
  release();
  release(); // повторное снятие не уводит счётчик в минус
  assert.equal(t.locked, false);
  scrolls.length = 0;
  t.follow(far);
  assert.equal(scrolls.length, 1, "после снятия замка слежение не вернулось");
});

check("два замка — снимаются оба, и только тогда слежение возвращается", () => {
  const t = follower(null, { headroom: 0 });
  const a = t.lock(), b = t.lock();
  a();
  scrolls.length = 0;
  t.follow(far);
  assert.equal(scrolls.length, 0, "один из двух замков снят, а слежение уже пошло");
  b();
  t.follow(far);
  assert.equal(scrolls.length, 1);
});

check("фокус в поле ввода держит слежение и без замка", () => {
  const t = follower(null, { headroom: 0 });
  const was = document.activeElement;
  document.activeElement = { tagName: "TEXTAREA" };
  scrolls.length = 0;
  t.follow(far);
  assert.equal(scrolls.length, 0, "человек печатает, а страница уехала");
  assert.equal(t.locked, true);
  document.activeElement = was;
  t.follow(far);
  assert.equal(scrolls.length, 1);
});

check("высота липкого блока может считаться на лету", () => {
  // headroom функцией: вёрстка меняется на узком экране, число в коде — нет
  let sticky = 100;
  const t = follower(null, { headroom: () => sticky });
  scrolls.length = 0;
  t.follow(far);
  const first = scrolls[0].top;
  sticky = 300;
  scrolls.length = 0;
  t.follow(far);
  assert.ok(scrolls[0].top < first, "изменение вёрстки не повлияло на центр чтения");
});

// --- режим правки ----------------------------------------------------------
//
// Правка и прослушивание не должны смешиваться: клик по слову перематывает, и это смысл
// читалки. Поэтому проверяем ровно границу между режимами, а не оформление.
console.log("\nрежим правки:");

const done = [];
const jumps = [];
const ed = buildKaraoke(TURNS, {
  onSeek: (sec) => jumps.push(sec),
  onEdit: (i, was, now) => done.push([i, was, now]),
});
const edWords = words(ed);
const lines = turnsOf(ed);
const buttonOf = (line) => line.querySelector(".kar-edit");
const bodyOf = (line) => line.kids.find((k) => k instanceof El && k.classList.contains("kar-text"));

check("вне режима клик по слову перематывает", () => {
  edWords[0]._on.click({ stopPropagation() {} });
  assert.deepEqual(jumps, [0], "перемотка по слову — смысл читалки, её трогать нельзя");
});

check("в режиме клик по слову ТОЖЕ перематывает", () => {
  // ⚠️ Правка владельца 08.09: правя реплику, человек первым делом хочет её переслушать.
  // Отобранная перемотка мешает ровно там, где она нужнее всего.
  ed.setEditing(true);
  jumps.length = 0;
  edWords[1]._on.click({ stopPropagation() {} });
  assert.deepEqual(jumps, [3], "в режиме правки перемотку по слову отобрали");
});

check("в режиме подсветка по времени идёт как обычно", () => {
  assert.ok(ed.at(3.5), "подсветка в режиме правки погасла");
});

check("у каждой реплики есть кнопка правки", () => {
  assert.ok(buttonOf(lines[0]) && buttonOf(lines[1]), "кнопка правки не у всех реплик");
});

check("нетронутые слова остаются СВОИМИ узлами и своим временем", () => {
  // Иначе поиск звучащего слова остался бы с висящими узлами, и в правленом абзаце умерли бы
  // и подсветка, и перемотка — ровно там, где человек только что работал.
  const line = lines[0];
  const before = line.querySelector(".kar-text").kids.filter((k) => k instanceof El);
  buttonOf(line).click();
  line.querySelector(".kar-area").value = "раз ДВА три";
  line.querySelector(".kar-ok").click();
  const after = line.querySelector(".kar-text").kids.filter((k) => k instanceof El);
  assert.equal(after[0], before[0], "«раз» перерисовали зря — его правка не касалась");
  assert.equal(after[2], before[2], "«три» перерисовали зря");
  assert.ok(after[1].classList.contains("kar-fresh"), "новое слово не помечено");
  jumps.length = 0;
  after[0]._on.click({ stopPropagation() {} });
  assert.deepEqual(jumps, [0], "перемотка по нетронутому слову потерялась");
  ed.at(0.5);
  assert.ok(after[0].classList.contains("on"), "подсветка после правки не находит слово");
  done.length = 0;
});

check("вставленное слово получает паузу между соседями и кликается", () => {
  const line = lines[1];
  buttonOf(line).click();
  line.querySelector(".kar-area").value = "четыре и пять";
  line.querySelector(".kar-ok").click();
  const kids = line.querySelector(".kar-text").kids.filter((k) => k instanceof El);
  jumps.length = 0;
  kids[1]._on.click({ stopPropagation() {} });
  assert.equal(jumps.length, 1, "вставленное слово не перематывает");
  assert.ok(jumps[0] >= 13 && jumps[0] <= 20, `вставленное слово вне паузы: ${jumps[0]}`);
  done.length = 0;
});

check("правка реплики целиком отдаётся как «было → стало»", () => {
  const line = lines[0];
  buttonOf(line).click();
  const area = line.querySelector("kar-area") || line.querySelector(".kar-area");
  assert.ok(area, "поле правки не открылось");
  area.value = "раз ДВА три четыре";
  line.querySelector(".kar-ok").click();
  assert.deepEqual(done, [[0, "раз ДВА три", "раз ДВА три четыре"]]);
});

check("отмена ничего не сохраняет и возвращает текст", () => {
  const line = lines[1];
  buttonOf(line).click();
  line.querySelector(".kar-area").value = "совсем другое";
  line.querySelector(".kar-no").click();
  assert.equal(done.length, 1, "«Отмена» записала правку");
  assert.ok(bodyOf(line), "текст реплики не вернулся на место");
});

check("выход из режима досохраняет открытое поле", () => {
  // Выход — жест сохранения. Потерять на нём набранный текст было бы худшим из ответов.
  const line = lines[1];
  buttonOf(line).click();
  line.querySelector(".kar-area").value = "четыре и пять шесть";
  ed.commitOpen();
  assert.deepEqual(done.at(-1), [1, "четыре и пять", "четыре и пять шесть"]);
});

// --- метка говорящего ------------------------------------------------------
//
// ⚠️ Раньше клик по метке уводил в верстак имён: человек, читая запись, вылетал со страницы и
// терял место. Теперь правка голоса — часть правки записи и живёт в её режиме.
console.log("\nметка говорящего:");

const позвали = [];
const sp = buildKaraoke(TURNS, { onSpeaker: (id, имя) => позвали.push([id, имя]) });
const метки = headsOf(sp)
  .flatMap((h) => h.kids.filter((x) => x instanceof El && x.classList.contains("kar-name")));

check("вне режима правки метка НИЧЕГО не делает", () => {
  метки[0]._on.click({ stopPropagation() {} });
  assert.deepEqual(позвали, [], "метка снова уводит со страницы посреди чтения");
});

check("в режиме правки метка открывает правку этого голоса", () => {
  sp.setEditing(true);
  метки[0]._on.click({ stopPropagation() {} });
  assert.deepEqual(позвали, [["Первый", "Первый"]]);
});

check("имя переписывается НА МЕСТЕ, без перезагрузки страницы", () => {
  // Перезагрузка оборвала бы воспроизведение, а человек правит подпись, слушая запись.
  sp.renameSpeaker("Первый", "Кузнецова");
  assert.equal(метки[0].textContent, "Кузнецова");
  assert.ok(!метки[0].classList.contains("raw"), "названный голос не «сырой»");
  sp.renameSpeaker("Первый", "");
  assert.equal(метки[0].textContent, "Первый", "снятие имени не вернуло метку");
  assert.ok(метки[0].classList.contains("raw"));
});

console.log(failures ? `\n${failures} провал(ов)` : "\nвсё зелёное");
process.exit(failures ? 1 : 0);
