// Главная: плоский список записей с фильтрами.
//
// Список ПЛОСКИЙ и по убыванию даты (решение владельца 10.09). Раздел, подраздел, год, метка и
// спикер — это не заголовки, а ФИЛЬТРЫ: искать запись человек начинает с признака, а не с
// прокрутки дерева. Отбор и сортировка — в `filter.js`, чтобы их проверял node-тест; здесь
// только рисование и связь с адресом.
//
// ⚠️ Состояние фильтров живёт В АДРЕСЕ (`?section=…&year=…`). Иначе отфильтрованным видом
// нельзя поделиться, а возврат из записи кнопкой «назад» терял бы отбор — человек каждый раз
// начинал бы заново.
import { $, el, fmtDate, fmtDuration, countOf } from "../ui/dom.js";
import { paintSection } from "../ui/theme.js";
import { canvasMeasurer, hashSeed, layoutWords, letterMask } from "./letter-cloud.js";
import { MAGNET, pull } from "./magnet.js";
import { coverUrl, getFrames, getRecords } from "../api.js";
import {
  EMPTY, MULTI, SORTS, applyFilters, facet, fromQuery, hasValue, isEmpty, listOf, sortFor,
  sortRecords, subAxis, withValue,
} from "./filter.js";

let loaded = null;
let state = { ...EMPTY };
let open = () => {};
// Куда вернуть прокрутку, придя назад из записи. Ключ — адрес с фильтрами: вернуться на
// прежнее место в ДРУГОМ отборе значит попасть в случайную точку чужого списка.
let scrollMemo = { key: "", y: 0 };

export async function renderRecords({ onOpen }) {
  open = onOpen;
  const list = $("#rec-list");
  if (!loaded) {
    list.replaceChildren(el("div", { class: "rec-sub", text: "Загружаю записи…" }));
    loaded = await getRecords();
    mountControls();
  }
  state = fromQuery(location.search);
  $("#f-find").value = state.q;
  $("#filters").hidden = loaded.records.length < 2;
  paint({ restore: true });
}

/** Постоянные узлы — вешаются один раз: пересоздание отняло бы фокус у поля ввода. */
function mountControls() {
  const sort = $("#f-sort");
  sort.replaceChildren(...SORTS.map((s) => el("option", { value: s.key, text: s.label })));
  sort.addEventListener("change", () => set({ sort: sort.value }));
  // «› ещё фильтры» — текстовая кнопка, панель под строкой поиска (владелец, 14.09).
  // ⚠️ Сама НЕ раскрывается — только кнопкой (владелец, 15.09: «их много, пользователь
  // теряется»): после перезагрузки панель свёрнута, даже если в адресе выбрана тема или
  // метка, — число выбранного стоит на самой кнопке. Раньше раскрывалась по ссылке с таким
  // отбором, и человек, выбравший тему и обновивший страницу, снова получал всю панель.
  $("#f-more-btn").addEventListener("click", () => {
    moreOpen = !moreOpen;
    paint();
  });

  const find = $("#f-find");
  // `input`, а не `change`: список обязан сужаться по мере набора — иначе поиск по подстроке
  // ощущается как форма, которую надо «отправить».
  find.addEventListener("input", () => set({ q: find.value }, { keepFocus: true }));
}

/** Правка состояния: адрес, потом перерисовка. `replaceState` — чтобы «назад» уводил со
 *  страницы, а не отматывал по одному нажатому чипу. */
function set(patch, { keepFocus = false } = {}) {
  // Подраздел принадлежит разделу: сменили раздел — прежний курс к нему не относится.
  if ("section" in patch && patch.section !== state.section) patch = { ...patch, sub: "" };
  state = { ...state, ...patch };
  history.replaceState(history.state, "", location.pathname + query());  // state — глубина истории роутера
  paint({ keepFocus });
}

const query = () => {
  const params = new URLSearchParams();
  for (const key of Object.keys(EMPTY)) {
    const value = (state[key] || "").trim();
    if (value) params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
};

function paint({ restore = false, keepFocus = false } = {}) {
  const records = loaded.records;
  const sort = sortFor(state, loaded.reading);
  const shown = sortRecords(applyFilters(records, state), sort);
  // ⚠️ Селект обязан показывать ДЕЙСТВУЮЩИЙ порядок, а не только выбранный руками: выбрав
  // курс, список сам разворачивается к первой лекции, и «сначала свежие» в селекте было бы
  // прямой ложью о том, что человек видит.
  $("#f-sort").value = sort;

  chips($("#f-sections"), "section", facet(records, state, "section"), "все разделы");
  const subs = subAxis(records, state);
  const subsRow = $("#f-subs");
  subsRow.hidden = !subs.length;
  if (subs.length) chips(subsRow, "sub", facet(records, state, "sub"), "весь раздел", subs);
  paintMore(records);
  chips($("#f-years"), "year", facet(records, state, "year"), "все годы", null, true);
  active();

  const hours = Math.round(shown.reduce((sum, r) => sum + (r.duration_sec || 0), 0) / 3600);
  $("#rec-count").textContent = shown.length
    ? `${countOf(shown.length, "запись", "записи", "записей")} · ${countOf(hours, "час", "часа", "часов")}`
    : "";

  const list = $("#rec-list");
  list.replaceChildren(...(shown.length ? shown.map(card) : [nothing()]));
  markClamped(list);

  if (keepFocus) $("#f-find").focus({ preventScroll: true });
  if (restore) restoreScroll();
}

// --- панель «ещё фильтры» --------------------------------------------------------------------
//
// Предмет и формат — чипы с МНОЖЕСТВЕННЫМ выбором (внутри измерения — ИЛИ), темы и метки —
// облака: кегль слова растёт с числом записей за ним (владелец, 14.09: «чем больше, тем крупнее
// шрифт, натуральное облако»). Порядок в облаке алфавитный, строки по центру — так крупные
// слова ложатся вразброс, и облако читается как облако, а не как список; найти слово при этом
// можно глазами, по алфавиту. Метки — 182 значения, 131 из них одноразовая: одноразовые
// спрятаны за «ещё N редких», иначе облако — простыня мелкого шрифта.
const PANEL_DIMS = ["category", "kind", "topic", "tag"];
let moreOpen = false;
let rareTags = false;

function paintMore(records) {
  const active = PANEL_DIMS.reduce((n, dim) => n + listOf(state[dim]).length, 0);
  const btn = $("#f-more-btn");
  btn.classList.toggle("on", moreOpen);
  btn.setAttribute("aria-expanded", String(moreOpen));
  // ⚠️ `replaceChildren` не пропускает null, а рисует текст «null» — отсеиваем до вставки.
  btn.replaceChildren(...[
    el("i", { text: moreOpen ? "⌄" : "›" }),
    ` ${moreOpen ? "скрыть фильтры" : "ещё фильтры"}`,
    active ? el("b", { text: String(active) }) : null,
  ].filter(Boolean));
  const panel = $("#f-more");
  panel.hidden = !moreOpen;
  if (!moreOpen) return;

  multiChips($("#f-cats"), "category", facet(records, state, "category"));
  multiChips($("#f-kinds"), "kind", facet(records, state, "kind"));
  // Темы — буква «М», метки — буква «О», рядом читаются как «МО» (владелец, 15.09); без
  // канваса (старый движок) — прежнее облако строками.
  letterCloud($("#f-topics"), "topic", facet(records, state, "topic"), "М");
  letterCloud($("#f-tags"), "tag", facet(records, state, "tag"), "О", { rareHidden: !rareTags, onRare: () => {
    rareTags = !rareTags;
    paint();
  } });
  for (const [block, box] of [["#fb-cats", "#f-cats"], ["#fb-kinds", "#f-kinds"], ["#fb-topics", "#f-topics"], ["#fb-tags", "#f-tags"]]) {
    $(block).hidden = !$(box).childElementCount;
  }
}

/** Чипы с множественным выбором: по убыванию частоты (у предметной оси нет «нового» и
 *  «старого»), выбранные всегда на месте — даже если при остальных фильтрах за ними ноль. */
function multiChips(row, dimension, counts) {
  const chosen = listOf(state[dimension]);
  const values = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || (a < b ? -1 : 1));
  for (const v of chosen) if (!values.includes(v)) values.push(v);
  row.replaceChildren(...values.map((value) => {
    const on = chosen.includes(value);
    return chip(value, counts.get(value) || 0, on, () => set({ [dimension]: withValue(state[dimension], value, !on) }));
  }));
}

/** Слова облака: вес 0..1 по логарифму числа записей, выбранные — всегда (даже при нуле),
 *  одноразовые метки — за «ещё N редких». Общее для облака строками и облака в форме буквы. */
function cloudEntries(dimension, counts, rareHidden) {
  const chosen = listOf(state[dimension]);
  let entries = [...counts.entries()];
  for (const v of chosen) if (!counts.has(v)) entries.push([v, 0]);
  const rare = rareHidden ? entries.filter(([v, n]) => n < 2 && !chosen.includes(v)) : [];
  if (rareHidden) entries = entries.filter(([v, n]) => n >= 2 || chosen.includes(v));
  entries.sort((a, b) => a[0].localeCompare(b[0], "ru"));
  const max = Math.max(1, ...entries.map(([, n]) => n));
  const scale = (n) => (max <= 1 ? 0 : Math.log(Math.max(1, n)) / Math.log(max)); // 0..1
  return { chosen, entries, rare, weight: scale };
}

/** Кнопка-слово облака: кегль и яркость по весу, клик — фильтр. */
function cloudWord(dimension, value, n, w, on, extraClass = "", style = "") {
  const node = el("button", {
    class: `fcw${on ? " on" : ""}${extraClass}`, type: "button", text: value,
    title: n ? `${countOf(n, "запись", "записи", "записей")}` : "сейчас записей нет",
    style: `font-size:${(11 + 15 * w).toFixed(1)}px;--w:${w.toFixed(2)};${style}`,
  });
  node.addEventListener("click", () => set({ [dimension]: withValue(state[dimension], value, !on) }));
  return node;
}

/** Кнопка «ещё N редких…» / «скрыть редкие» — или null, если показывать нечего. */
function rareToggle(rare, entries, rareHidden, onRare) {
  if (!onRare) return null;
  if (rare.length) {
    const more = el("button", { class: "fcw fcw-rare", type: "button", text: `ещё ${countOf(rare.length, "редкая", "редкие", "редких")}…` });
    more.addEventListener("click", onRare);
    return more;
  }
  if (!rareHidden && entries.some(([, n]) => n < 2)) {
    const less = el("button", { class: "fcw fcw-rare", type: "button", text: "скрыть редкие" });
    less.addEventListener("click", onRare);
    return less;
  }
  return null;
}

/** Облако слов строками: кегль по логарифму числа записей (11-26 px), цвет от бледного к яркому. */
function cloud(box, dimension, counts, { rareHidden = false, onRare = null } = {}) {
  const { chosen, entries, rare, weight } = cloudEntries(dimension, counts, rareHidden);
  const nodes = entries.map(([value, n]) => cloudWord(dimension, value, n, weight(n), chosen.includes(value)));
  const toggle = rareToggle(rare, entries, rareHidden, onRare);
  if (toggle) nodes.push(toggle);
  box.classList.remove("lcloud");
  box.style.height = "";
  box.replaceChildren(...nodes);
}

/**
 * Облако в форме БУКВЫ (владелец, 15.09): слова разложены по маске глифа, часть — вертикально
 * и под углом, чтобы заполнились штрихи и буква читалась издалека. Геометрия — в
 * `letter-cloud.js`; здесь только слова, размер поля и кнопки. Ширина поля — по факту (панель
 * уже показана, ширина есть), высота — 0.85 ширины: и «М», и «О» в такой пропорции ложатся.
 * Слова, которым не нашлось места, не пропадают: они дописываются строкой под буквой.
 */
function letterCloud(box, dimension, counts, letter, { rareHidden = false, onRare = null } = {}) {
  const width = Math.min(460, Math.max(240, box.clientWidth || 400));
  // Квадрат. Вытянутое поле с растяжением глифа (1.25 ширины) пробовали 15.09 — владелец:
  // «некрасиво, давай более квадратные»; место под слова добирается ужиманием кегля ниже.
  const height = width;
  const root = getComputedStyle(document.documentElement);
  const serif = root.getPropertyValue("--serif").trim() || "Georgia, serif";
  const sans = root.getPropertyValue("--sans").trim() || "system-ui, sans-serif";
  // Обводка в 10 % ширины поля: штрихи «М» и «О» жирнее самого жирного веса шрифта
  // (владелец, 15.09: «толщину букв тоже больше») — и площадь под слова заметно больше.
  const mask = letterMask(letter, { width, height, cell: 3, family: sans, stroke: Math.round(width * 0.1) });
  const measure = canvasMeasurer(serif);
  if (!mask || !measure) return cloud(box, dimension, counts, { rareHidden, onRare });

  const { chosen, entries, rare, weight } = cloudEntries(dimension, counts, rareHidden);
  const words = entries.map(([value, n]) => ({ value, weight: weight(n), n }));
  const byValue = new Map(words.map((w) => [w.value, w]));
  // Потолок кегля 22, не 26: замерено в странице на 91 теме — при 24 не влезают 13 слов,
  // при 22 четыре; крупные слова съедают площадь буквы быстрее, чем добавляют читаемости.
  // Не влезли все — потолок ужимается на два пункта и раскладка считается заново (≈60 мс за
  // проход): владелец просил, чтобы темы влезли в «М» ВСЕ; хвост под буквой — крайний случай.
  const seed = hashSeed(entries.map(([v, n]) => `${v}:${n}`));
  const top = width < 360 ? 19 : 22;
  let placed = [];
  let dropped = [];
  for (const cap of [top, top - 2, top - 4, top - 6]) {
    ({ placed, dropped } = layoutWords(mask, words, measure, { seed, minSize: 10, maxSize: cap }));
    if (!dropped.length) break;
  }
  const nodes = placed.map((p) => cloudWord(
    dimension, p.value, byValue.get(p.value).n, p.weight, chosen.includes(p.value), " lc",
    `left:${p.x.toFixed(1)}px;top:${p.y.toFixed(1)}px;font-size:${p.size.toFixed(1)}px;--rot:${p.angle}deg`,
  ));
  // Не влезло в букву — строкой под ней: слово из фильтра пропасть не может.
  const tail = dropped.map((v) => cloudWord(dimension, v, byValue.get(v).n, byValue.get(v).weight, chosen.includes(v)));
  const toggle = rareToggle(rare, entries, rareHidden, onRare);
  const under = tail.length || toggle ? el("div", { class: "lc-under" }, ...tail, toggle) : null;
  box.classList.add("lcloud");
  box.style.height = `${height}px`;
  box.replaceChildren(...nodes);
  magnetize(box, placed.map((p, i) => ({ node: nodes[i], x: p.x, y: p.y })));
  // Хвост — соседом, а не внутрь: внутри поля всё абсолютно позиционировано.
  box.nextElementSibling?.classList.contains("lc-under") && box.nextElementSibling.remove();
  if (under) box.after(under);
}

// Слова рядом с курсором растут и тянутся к нему (владелец, 15.09). Центры слов известны из
// раскладки — DOM не меряем; на каждый кадр — только запись трёх переменных в стиль тех слов,
// что в радиусе, остальным сбрасываем один раз. Только для мыши: на тачскрине курсора нет,
// а «прыгающие» под пальцем слова мешали бы попасть. Слушатели переживают перерисовку:
// вешаются на поле один раз, а слова берут из последней раскладки.
const magnets = new WeakMap();
function magnetize(box, words) {
  if (!matchMedia?.("(hover: hover) and (pointer: fine)").matches) return;
  if (magnets.has(box)) {
    magnets.get(box).words = words;
    return;
  }
  const m = { words, raf: 0, pointer: null, touched: new Set() };
  magnets.set(box, m);
  const set = (w, { x, y, s }) => {
    w.node.style.setProperty("--mx", `${x.toFixed(1)}px`);
    w.node.style.setProperty("--my", `${y.toFixed(1)}px`);
    w.node.style.setProperty("--ms", s.toFixed(3));
  };
  const frame = () => {
    m.raf = 0;
    const next = new Set();
    if (m.pointer) {
      for (const w of m.words) {
        const dx = m.pointer.x - w.x, dy = m.pointer.y - w.y;
        if (Math.abs(dx) > MAGNET.radius || Math.abs(dy) > MAGNET.radius) continue;
        const p = pull(dx, dy);
        if (p.s === 1) continue;
        set(w, p);
        next.add(w);
      }
    }
    for (const w of m.touched) if (!next.has(w)) set(w, { x: 0, y: 0, s: 1 });
    m.touched = next;
  };
  box.addEventListener("pointermove", (event) => {
    const r = box.getBoundingClientRect();
    m.pointer = { x: event.clientX - r.left, y: event.clientY - r.top };
    m.raf ||= requestAnimationFrame(frame);
  });
  box.addEventListener("pointerleave", () => {
    m.pointer = null;
    m.raf ||= requestAnimationFrame(frame);
  });
}

/** Ряд чипов одного измерения. Ноль записей — чипа нет вовсе: выбор, ведущий в пустоту,
 *  выглядит поломкой. Выбранный остаётся всегда, иначе с него некуда вернуться.
 *
 *  ⓘ Без явного порядка чипы идут ПО СВЕЖЕСТИ: `facet` считает по списку, отсортированному по
 *  дате, и порядок ключей — это порядок первого появления. Алфавит поставил бы наверх раздел,
 *  где ничего не происходило годами. */
function chips(row, dimension, counts, allLabel, order = null, descending = false) {
  const values = order || (descending ? [...counts.keys()].sort((a, b) => (a < b ? 1 : -1))
                                      : [...counts.keys()]);
  const nodes = [chip(allLabel, "", !state[dimension], () => set({ [dimension]: "" }))];
  for (const value of values) {
    const count = counts.get(value) || 0;
    if (!count && state[dimension] !== value) continue;
    nodes.push(chip(value, count, state[dimension] === value, () =>
      set({ [dimension]: state[dimension] === value ? "" : value }),
      // Чип раздела — в цвете ветки: точка перед именем, выбранный заливается им.
      dimension === "section" ? value : ""));
  }
  row.replaceChildren(...nodes);
}

function chip(label, count, on, onClick, section = "") {
  const node = el(
    "button",
    { class: `fchip${on ? " on" : ""}`, type: "button" },
    el("span", { text: label }),
    count ? el("i", { text: String(count) }) : null
  );
  if (section) paintSection(node, section);
  node.addEventListener("click", onClick);
  return node;
}

/** Метка и спикер выбираются КЛИКОМ ПО КАРТОЧКЕ (их 182 и 16 — рядами не выложить),
 *  поэтому выбранное показываем отдельной строкой с крестиком: иначе непонятно, почему
 *  список короткий, и нечем это снять. */
function active() {
  const row = $("#f-active");
  const nodes = [];
  // ⓘ Подпись «спикер», а не «человек» (владелец, 14.09): измерение ищет и по выступавшим, и по
  // участникам, но на сайте эту ось называют спикером — так же подписан подстрочный поиск.
  for (const [dimension, prefix] of [["category", "предмет"], ["topic", "тема"], ["tag", "метка"], ["speaker", "спикер"], ["kind", "формат"]]) {
    // У измерений с множественным выбором — по чипу на значение, крестик снимает только его.
    const values = MULTI.has(dimension) ? listOf(state[dimension]) : [state[dimension]].filter(Boolean);
    for (const value of values) {
      const node = el(
        "button",
        { class: "fchip on drop", type: "button", title: `Снять фильтр: ${prefix}` },
        el("span", { text: `${prefix}: ${value}` }),
        el("i", { class: "x", text: "✕" })
      );
      node.addEventListener("click", () =>
        set({ [dimension]: MULTI.has(dimension) ? withValue(state[dimension], value, false) : "" }));
      nodes.push(node);
    }
  }
  if (!isEmpty(state)) {
    const reset = el("button", { class: "fchip reset", type: "button", text: "сбросить всё" });
    reset.addEventListener("click", () => {
      state = { ...EMPTY };
      $("#f-find").value = "";
      history.replaceState(history.state, "", location.pathname);
      paint();
    });
    nodes.push(reset);
  }
  row.hidden = !nodes.length;
  row.replaceChildren(...nodes);
}

// --- обложка со слайдшоу ---------------------------------------------------------------------
//
// Наведение на обложку через 2 с запускает пролистывание кадров записи (владелец, 14.09): по
// кадру в 1.4 с, по кругу; уход курсора возвращает обложку. Кадры — сырые `slides/sNNN.jpg`, в
// них есть полоса миниатюр участников и подпись говорящего (лица, фамилии), поэтому каждый кадр
// показывается через ТУ ЖЕ рамку, что вырезала обложку (`crop` из `/frames`, доли кадра):
// рамка у записи одна на все кадры — раскладка экрана в созвоне не меняется.
const SHOW_AFTER_MS = 2000;
const FRAME_MS = 1400;
const framesCache = new Map();

function fetchFrames(id) {
  if (!framesCache.has(id)) {
    framesCache.set(id, getFrames(id).catch(() => ({ frames: [], crop: null })));
  }
  return framesCache.get(id);
}

/** Вписать рамку `box` (доли кадра) в обёртку так, чтобы она заполнила её целиком, как
 *  `object-fit: cover`, только для ПОДобласти кадра: считаем по натуральному размеру картинки. */
function fitBox(img, wrap, box) {
  const [x0, y0, x1, y1] = box;
  const W = img.naturalWidth, H = img.naturalHeight;
  if (!W || !H) return;
  const bw = (x1 - x0) * W, bh = (y1 - y0) * H;
  const scale = Math.max(wrap.clientWidth / bw, wrap.clientHeight / bh);
  Object.assign(img.style, {
    position: "absolute", objectFit: "fill", maxWidth: "none",
    width: `${W * scale}px`, height: `${H * scale}px`,
    left: `${-x0 * W * scale - (bw * scale - wrap.clientWidth) / 2}px`,
    top: `${-y0 * H * scale - (bh * scale - wrap.clientHeight) / 2}px`,
  });
}

function coverWithSlideshow(record) {
  const img = el("img", { class: "rec-cover-img", src: coverUrl(record.id, record.cover), alt: "", loading: "lazy" });
  const wrap = el("div", { class: "rec-cover" }, img);
  let armed = null, ticker = null, i = 0, frames = [], crop = null, showing = false;
  // рамка применяется при загрузке КАЖДОГО кадра: натуральный размер известен только тогда;
  // слушатель один на обложку, а не на каждое наведение
  img.addEventListener("load", () => { if (showing && crop) fitBox(img, wrap, crop); });

  const restore = () => {
    clearTimeout(armed); clearInterval(ticker);
    armed = ticker = null;
    if (showing) {
      showing = false;
      img.removeAttribute("style");
      img.src = coverUrl(record.id, record.cover);
    }
  };
  const tick = () => {
    const f = frames[i++ % frames.length];
    img.src = coverUrl(record.id, f.frame);
    img.title = f.title || "";
  };
  const start = async () => {
    ({ frames, crop } = await fetchFrames(record.id));
    if (!frames.length || !armed) return;             // курсор уже ушёл — не стартуем
    showing = true;
    i = 0;
    tick();
    ticker = setInterval(tick, FRAME_MS);
  };
  wrap.addEventListener("mouseenter", () => { restore(); armed = setTimeout(start, SHOW_AFTER_MS); });
  wrap.addEventListener("mouseleave", () => { restore(); img.title = ""; });
  return wrap;
}

function nothing() {
  return el(
    "div",
    { class: "rec-empty" },
    el("b", { text: "Ничего не нашлось" }),
    el("span", { text: "Попробуйте снять часть фильтров или спросить у поиска вверху страницы." })
  );
}

// --- прокрутка --------------------------------------------------------------
//
// Уйти в запись и вернуться в начало списка из 188 карточек — это потерять место. Помним
// смещение и возвращаем его, но только если отбор тот же.

/** Свернуть «ещё фильтры» — зовёт знак в шапке: он значит «главная с чистого листа».
 *  Возврат из записи («К записям», «назад» браузера) панель НЕ трогает: там продолжают отбор. */
export function collapseFilters() {
  if (!moreOpen) return;
  moreOpen = false;
  if (loaded) paint();
}

export function rememberScroll() {
  scrollMemo = { key: location.pathname + location.search, y: window.scrollY };
}

/** Забыть место: возвращаясь к ПОЛЮ ВОПРОСА (с диалога, по кнопке в шапке), человек не
 *  хочет попасть в середину списка, откуда когда-то открыл запись. */
export function forgetScroll() {
  scrollMemo = { key: "", y: 0 };
}

function restoreScroll() {
  const key = location.pathname + query();
  if (scrollMemo.key !== key || !scrollMemo.y) return;
  const y = scrollMemo.y;
  // ⚠️ Одного кадра НЕ ХВАТАЕТ: замеряно — список уже в документе, но высота ещё нулевая, и
  // браузер обрезает прокрутку до нуля. Поэтому кладём несколько раз, форсируя пересчёт
  // высоты чтением, — тот же приём, что у читалки при переходе на момент.
  let touched = false;
  const stop = () => { touched = true; };
  for (const type of ["wheel", "touchmove", "keydown"]) {
    addEventListener(type, stop, { passive: true, once: true });
  }
  const put = () => {
    if (touched) return;              // человек уже листает сам — не дёргаем страницу
    void document.body.offsetHeight;  // форсируем пересчёт: без него scrollTo упрётся в ноль
    window.scrollTo(0, y);
  };
  requestAnimationFrame(put);
  setTimeout(put, 60);
  setTimeout(put, 250);
}

// --- карточка ---------------------------------------------------------------

/** «… ››» — только у обрезанных аннотаций. Обрезку решает раскладка (ширина колонки, длина
 * заголовка), поэтому меряем после отрисовки: сперва ЧТЕНИЕ у всех (один пересчёт раскладки на
 * 190 карточек), потом запись — иначе чтение-запись вперемешку пересчитывало бы её на каждой. */
function markClamped(root) {
  const nodes = [...root.querySelectorAll(".rec-summary:not(.open)")];
  const clipped = nodes.map((n) => n.scrollHeight > n.clientHeight + 1);
  nodes.forEach((n, i) => {
    const more = n.querySelector(".rec-more");
    if (more) more.hidden = !clipped[i];
  });
}

function card(record) {
  // На карточке — только ВЫСТУПАВШИЕ. Участники живут на странице записи: ведущий встречи
  // один и тот же на десятках карточек, и в списке это шум, а не признак.
  const speakers = (record.speakers || []).map((name) =>
    pick("speaker", name, "rec-who"));
  // Формат — чип перед темами: он же фильтр, кликом. Темы — из словаря корпуса, ПЕРВЫЕ ТРИ:
  // иначе карточка обрастёт, как с метками сайта (они остались в данных, но с витрины ушли —
  // 72% одноразовых и фамилии вместо тем; решение владельца 12.09).
  const kinds = (record.kind || []).map((kind) => pick("kind", kind, "chip-topic chip-kind"));
  const tags = (record.topics || []).slice(0, 3).map((topic) => pick("topic", topic, "chip-topic"));

  // Длительность — мелкий признак перед датой (владелец, 14.09): слева теперь обложка, а
  // «53 мин» рядом с датой читается как свойство записи, а не как её номер.
  const sub = el(
    "div",
    { class: "rec-sub" },
    record.duration_sec ? el("span", { class: "rec-dur", text: fmtDuration(record.duration_sec) }) : null,
    record.duration_sec ? el("span", { class: "dot" }) : null,
    fmtDate(record.date),
    speakers.length ? el("span", { class: "dot" }) : null,
    speakers.length ? el("span", { class: "rec-people" }, ...join(speakers)) : null,
    kinds.length || tags.length ? el("span", { class: "dot" }) : null,
    kinds.length || tags.length ? el("span", { class: "rec-topics" }, ...kinds, ...tags) : null
  );

  // Слева — ОБЛОЖКА, крупно, чтобы слайд читался (владелец, 14.09): кадр титульного слайда,
  // выбранный конвейером экрана (`cover` из сайдкара, уже обрезанный от миниатюр участников).
  // Где кадра нет (запись без видео или без слайдов) — нейтральная плашка того же размера, чтобы
  // список не разъезжался. В левой колонке у подкаста стоял номер выпуска, потом длительность —
  // она ушла к дате.
  const cover = record.cover ? coverWithSlideshow(record) : el("div", { class: "rec-cover rec-cover-none" });
  // Аннотация из поста, а где её нет — краткое содержание по расшифровке (`blurb`, тот же
  // текст, что на странице записи). Пустой строки не рисуем вовсе: список выглядел бы дырявым.
  // До пяти строк, дальше «… ››» разворачивает текст ПРЯМО НА КАРТОЧКЕ (владелец, 14.09) — не
  // уходя в запись. Кнопка показывается только у обрезанных: `markClamped` меряет после
  // отрисовки. Клик по ней не должен открывать запись — вся карточка ссылка.
  const summary = record.summary || record.blurb;
  const more = summary
    ? el("button", { class: "rec-more", type: "button", text: "… ››", title: "Показать весь текст", hidden: "" })
    : null;
  const summaryNode = summary ? el("div", { class: "rec-summary" }, el("span", { text: summary }), more) : null;
  more?.addEventListener("click", (event) => {
    event.stopPropagation();
    const opened = summaryNode.classList.toggle("open");
    more.textContent = opened ? "‹‹ свернуть" : "… ››";
    more.title = opened ? "Свернуть" : "Показать весь текст";
  });
  const node = el(
    "article",
    { class: "rec-card" },
    cover,
    el(
      "div",
      { class: "rec-main" },
      el("div", { class: "rec-title" },
        // Отметка «лучшее» из источника корпуса — знаком, без пояснений: смысл задаёт корпус.
        record.award ? el("span", { class: "rec-award", text: "🏆", title: "Отмечено" }) : null,
        record.title),
      summaryNode
    ),
    // Ни скачивания, ни стрелки «›» на карточке (владелец, 14.09): вся карточка — ссылка, а
    // расшифровку скачивают со страницы записи. Место — тексту.
    sub
  );
  // Цвет ветки — рамкой карточки и всем, что внутри неё нарисовано акцентом (длительность).
  paintSection(node, record.section);
  node.addEventListener("click", () => {
    rememberScroll();
    open(record.id);
  });
  return node;
}

/** Метка и спикер на карточке — они же фильтр. ⚠️ Клик обязан остановить всплытие: иначе он
 *  и отфильтрует список, и откроет запись, а человек увидит только второе. */
function pick(dimension, value, cls) {
  // У измерений с множественным выбором клик ДОБАВЛЯЕТ значение к выбранным, у одиночных —
  // заменяет; повторный клик снимает в обоих случаях.
  const multi = MULTI.has(dimension);
  const on = multi ? hasValue(state[dimension], value) : state[dimension] === value;
  const node = el("button", {
    class: `${cls} pick${on ? " on" : ""}`,
    type: "button",
    text: value,
    title: multi ? `Отобрать и это (${value})` : `Показать только это (${value})`,
  });
  node.addEventListener("click", (event) => {
    event.stopPropagation();
    set({ [dimension]: multi ? withValue(state[dimension], value, !on) : on ? "" : value });
    window.scrollTo(0, 0);
  });
  return node;
}

// Запятая — обычный текстовый узел между кнопками. ⚠️ Зазор во флексе тут не годится: он
// отодвигает запятую от имени, и получается «Ковалёв , Кузнецова».
const join = (nodes) =>
  nodes.flatMap((node, i) => (i ? [document.createTextNode(", "), node] : [node]));
