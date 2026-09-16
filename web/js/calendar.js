// Календарь выступлений: прошлое — из самих записей (дата, докладчики, формат), будущее — из
// `calendar.json` пространства (`/api/calendar`: ближайшие встречи и идеи). Год — ОДНИМ
// экраном: двенадцать месяцев по четыре в ряд, январь → декабрь (владелец, 14.09: «компактнее»);
// выступление стоит на своей дате подсвеченным числом, а список выступлений дня показывает
// всплывашка по клику на это число. Сетка, а не лента: встречи идут раз в две недели, и на
// сетке видно ритм и пропуски, которых в списке не разглядеть; а списки под каждым месяцем
// растягивали год на три экрана.
import { $, el, countOf } from "./ui/dom.js";
import { paintSection } from "./ui/theme.js";
import { getRecords, getCalendar } from "./api.js";

const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
                    "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

let loaded = null;     // {records, reading}
let future = null;     // ответ /api/calendar
let open = () => {};
let goYear = () => {};

export async function renderCalendar(year, { onOpen, onYear } = {}) {
  open = onOpen || open;
  goYear = onYear || goYear;
  if (!loaded) loaded = await getRecords();
  if (!future) future = await getCalendar().catch(() => ({ upcoming: [], ideas: [] }));

  const dated = loaded.records.filter((r) => /^\d{4}-\d{2}-\d{2}/.test(r.date));
  const years = [...new Set(dated.map((r) => r.date.slice(0, 4)))].sort().reverse();
  const current = years.includes(year) ? year : years[0] || String(new Date().getFullYear());
  const inYear = dated.filter((r) => r.date.startsWith(current));

  $("#cal-head").textContent = `${countOf(inYear.length, "запись", "записи", "записей")} за ${current}`;
  renderUpcoming($("#cal-upcoming"), future);
  renderYears($("#cal-years"), years, current);
  renderYear($("#cal-grid"), inYear, Number(current));
}

/** Будущее: только ближайшие встречи. Пусто — блока нет, а не «пока пусто».
 * Предложка идей и строка «Источник … · снимок …» с календаря сняты (владелец, 14.09): идеи —
 * не расписание, а откуда снимок, посетителю знать незачем; данные в `/api/calendar` остались. */
function renderUpcoming(box, data) {
  const upcoming = data.upcoming || [];
  if (!upcoming.length) {
    box.replaceChildren();
    return;
  }
  box.replaceChildren(
    el("h3", { class: "cal-cap", text: "Ближайшие" }),
    el("div", { class: "cal-list" }, ...upcoming.map((e) =>
      el("div", { class: "cal-item" },
        el("span", { class: "cal-date", text: fmtDay(e.date) }),
        el("span", { class: "cal-title", text: e.title }),
        e.people?.length ? el("span", { class: "cal-people", text: e.people.join(", ") }) : null))));
}

function renderYears(row, years, current) {
  row.replaceChildren(...years.map((y) => {
    const chip = el("button", { class: `fchip${y === current ? " on" : ""}`, type: "button", text: y });
    chip.addEventListener("click", () => goYear(y));
    return chip;
  }));
}

/** Год целиком, все двенадцать месяцев: пустые (в том числе будущие) тоже рисуем — сетка
 * четыре в ряд держится только при полном наборе, а пустой месяц сам по себе информация. */
function renderYear(box, records, year) {
  closePop();
  const byDay = new Map();
  for (const r of records) {
    const key = r.date.slice(0, 10);
    byDay.set(key, [...(byDay.get(key) || []), r]);
  }
  const today = new Date().toISOString().slice(0, 10);
  box.replaceChildren(...MONTHS.map((_, m) => month(box, year, m, byDay, today)));
  armDismiss();
}

function month(box, year, m, byDay, today) {
  const first = new Date(year, m, 1);
  const days = new Date(year, m + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7; // неделя с понедельника
  const cells = WEEKDAYS.map((d) => el("span", { class: "cal-wd", text: d }));
  for (let i = 0; i < lead; i += 1) cells.push(el("span", { class: "cal-day empty" }));
  for (let d = 1; d <= days; d += 1) {
    const key = `${year}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const here = (byDay.get(key) || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    const cell = el(here.length ? "button" : "span",
      { class: `cal-day${here.length ? " has" : ""}${key > today ? " future" : ""}`, text: String(d) });
    if (here.length) {
      cell.type = "button";
      cell.title = here.map((r) => r.title).join("\n");
      // День — в цвете ветки выступления (при нескольких — первого по времени).
      paintSection(cell, here[0].section);
      // Клик по подсвеченному числу — всплывашка с выступлениями дня (владелец, 14.09); в
      // запись ведёт уже строка в ней. Раньше день открывал первую запись сразу, а остальные
      // жили в списке под месяцем — списков больше нет.
      cell.addEventListener("click", (event) => {
        event.stopPropagation();
        showPop(box, cell, key, here);
      });
      if (here.length > 1) cell.append(el("i", { text: String(here.length) }));
    }
    cells.push(cell);
  }
  return el("section", { class: "cal-month" },
    el("h3", { class: "cal-cap", text: MONTHS[m] }),
    el("div", { class: "cal-grid" }, ...cells));
}

// --- всплывашка дня ----------------------------------------------------------
//
// Одна на страницу, живёт внутри `#cal-grid` (он `position:relative`) и ставится ПОД нажатым
// числом; у правого края сдвигается влево, чтобы не вылезти за сетку. Закрывается кликом мимо,
// Escape, повторным кликом по тому же дню и при перерисовке года.
let pop = null;
let popKey = "";
let dismissArmed = false;

function closePop() {
  pop?.remove();
  pop = null;
  popKey = "";
  document.querySelector(".cal-day.sel")?.classList.remove("sel");
}

function armDismiss() {
  if (dismissArmed) return;
  dismissArmed = true;
  document.addEventListener("click", closePop);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePop();
  });
}

function showPop(box, cell, key, records) {
  if (popKey === key) {
    closePop();
    return;
  }
  closePop();
  popKey = key;
  cell.classList.add("sel");
  const rows = records.map((r) => {
    const row = el("button", { class: "cal-item cal-rec", type: "button" },
      el("span", { class: "cal-item-sec", text: r.section || "" }),
      el("span", { class: "cal-title" }, r.award ? "🏆 " : "", r.title),
      (r.speakers || []).length ? el("span", { class: "cal-people", text: r.speakers.join(", ") }) : null,
      (r.kind || []).length ? el("span", { class: "chip-topic chip-kind", text: r.kind[0] }) : null);
    paintSection(row, r.section); // в один день бывают выступления разных веток — каждая своим цветом
    row.addEventListener("click", () => {
      closePop();
      open(r.id);
    });
    return row;
  });
  const close = el("button", { class: "cal-pop-close", type: "button", text: "×", title: "Закрыть",
                               "aria-label": "Закрыть" });
  close.addEventListener("click", closePop);
  pop = el("div", { class: "cal-pop", role: "dialog" },
    el("div", { class: "cal-pop-head" }, el("span", { class: "cal-pop-date", text: fmtFull(key) }), close),
    el("div", { class: "cal-list" }, ...rows));
  pop.addEventListener("click", (event) => event.stopPropagation());
  paintSection(pop, records[0].section); // дата в шапке всплывашки — цветом ветки дня
  box.append(pop);
  const b = box.getBoundingClientRect();
  const c = cell.getBoundingClientRect();
  const width = Math.min(340, b.width);
  let left = c.left - b.left;
  if (left + width > b.width) left = Math.max(0, b.width - width);
  pop.style.left = `${Math.round(left)}px`;
  pop.style.top = `${Math.round(c.bottom - b.top + 6)}px`;
  pop.style.width = `${Math.round(width)}px`;
  rows[0]?.focus();
}

function fmtDay(iso) {
  if (!/^\d{4}-\d{2}-\d{2}/.test(iso || "")) return iso || "";
  const d = new Date(iso.slice(0, 10) + "T00:00:00");
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
}

function fmtFull(iso) {
  return `${fmtDay(iso)} ${iso.slice(0, 4)}`;
}
