// Фильтры и сортировка списка записей — ЧИСТОЕ ядро, без DOM.
//
// Весь корпус приезжает одним ответом (188 записей — 156 КБ) и целиком лежит на клиенте,
// поэтому фильтрация мгновенная и без сервера. Здесь только состояние, отбор, сортировка и
// подсчёт фасетов; рисование — в `list.js`, чтобы это можно было проверить node-тестом.
//
// Фасеты у нас РАЗНОЙ ПРИРОДЫ, и обращение с ними разное:
//   раздел (4 значения) и год (5) — чипы, их видно все сразу;
//   подраздел — вторая ось выбранного раздела: у митапов это год (дубль, не показываем),
//   у курса — сам курс, и вот он единственная осмысленная ось;
//   метка (182 значения, 131 из них одноразовая) и спикер — чипами не выложить и списком не
//   спасти: включаются КЛИКОМ по метке на карточке, где они и так нарисованы.

/** Пустое состояние фильтров. `sort` пустой — «как решит раздел» (см. `sortFor`). */
export const EMPTY = { section: "", sub: "", year: "", category: "", topic: "", tag: "", speaker: "", kind: "", q: "", sort: "" };

/**
 * Измерения с МНОЖЕСТВЕННЫМ выбором (владелец, 14.09: «нельзя выбрать несколько категорий»):
 * значения внутри одного измерения соединяются ИЛИ, измерения между собой — И. В адресе и в
 * состоянии список хранится строкой через `|` — ключи и форма адреса не меняются, ссылка с
 * одним значением читается как раньше. Раздел, подраздел, год и человек остаются одиночными:
 * раздел и год — оси списка, а не признаки, и «два раздела разом» это просто «все разделы».
 */
export const MULTI = new Set(["category", "kind", "topic", "tag"]);
export const SEP = "|";
export const listOf = (value) => String(value || "").split(SEP).map((s) => s.trim()).filter(Boolean);
export const hasValue = (value, item) => listOf(value).includes(item);
/** Список с включённым или выключенным элементом — строкой, как хранится в состоянии. */
export function withValue(value, item, on) {
  const rest = listOf(value).filter((x) => x !== item);
  if (on) rest.push(item);
  return rest.join(SEP);
}

/** Все люди записи, какой бы ни была роль: по человеку ищут, не зная, выступал он или спрашивал. */
export const peopleOf = (record) => [...(record.speakers || []), ...(record.participants || [])];

export const SORTS = [
  { key: "new", label: "сначала свежие" },
  { key: "old", label: "сначала старые" },
  { key: "title", label: "по названию" },
  { key: "long", label: "сначала длинные" },
];

/** Число как число, а не как строка: «занятие-10» обязано идти после «занятие-2». */
const natural = (text) =>
  String(text).split(/(\d+)/).map((part) => (/^\d+$/.test(part) ? Number(part) : part));

function naturalCmp(a, b) {
  const x = natural(a);
  const y = natural(b);
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    const l = x[i];
    const r = y[i];
    if (l === undefined) return -1;
    if (r === undefined) return 1;
    if (l === r) continue;
    return typeof l === "number" && typeof r === "number" ? l - r : String(l) < String(r) ? -1 : 1;
  }
  return 0;
}

/** Всё, по чему ищет строка поиска. Заголовка мало: спрашивают и «кто», и «про что». */
const haystack = (record) =>
  [record.title, record.summary, ...peopleOf(record), ...(record.tags || []), ...(record.kind || []),
   record.category || "", ...(record.topics || [])]
    .join(" ")
    .toLowerCase();

/** Год записи: поле `year`, если корпус его дал (у курсов дата — дата выкладки), иначе из даты. */
const yearOf = (record) => record.year || String(record.date).slice(0, 4);

export function applyFilters(records, state) {
  const s = { ...EMPTY, ...state };
  const needle = s.q.trim().toLowerCase();
  const wanted = {};
  for (const dim of MULTI) wanted[dim] = listOf(s[dim]);
  return records.filter((r) => {
    if (s.section && r.section !== s.section) return false;
    if (s.sub && r.subgroup !== s.sub) return false;
    if (s.year && yearOf(r) !== s.year) return false;
    if (s.speaker && !peopleOf(r).includes(s.speaker)) return false;
    // Множественный выбор: хотя бы одно из выбранных значений измерения есть у записи.
    for (const dim of MULTI) {
      const want = wanted[dim];
      if (want.length && !valuesOf(r, dim).some((v) => want.includes(v))) return false;
    }
    if (needle && !haystack(r).includes(needle)) return false;
    return true;
  });
}

/**
 * Порядок по умолчанию для выбранного раздела.
 *
 * ⚠️ Курс читают ПОДРЯД, и «сначала свежие» показывает лекцию 25 первой. Направление приходит
 * с сервера (`reading.sections`) — из данных его не вывести: даты у лекций одинаковые, а
 * «читается как рассказ» это решение владельца, а не свойство записей.
 */
export function sortFor(state, reading = {}) {
  if (state.sort) return state.sort;
  const sections = reading.sections || {};
  const direction = state.section ? sections[state.section] : reading.default;
  return direction === "asc" ? "old" : "new";
}

export function sortRecords(records, sort) {
  // Ничья по дате разрывается номером ПО ВОЗРАСТАНИЮ в обе стороны: у курса все занятия
  // выложены одним днём, то есть ничья там не редкий случай, а весь курс целиком.
  const out = [...records].sort((a, b) => naturalCmp(a.id, b.id));
  if (sort === "title") return out.sort((a, b) => naturalCmp(a.title, b.title));
  if (sort === "long") return out.sort((a, b) => (b.duration_sec || 0) - (a.duration_sec || 0));
  const sign = sort === "old" ? 1 : -1;
  return out.sort((a, b) => (a.date < b.date ? -sign : a.date > b.date ? sign : 0));
}

/**
 * Значения фасета и сколько записей за каждым — при ОСТАЛЬНЫХ действующих фильтрах.
 *
 * Считаем без своего измерения намеренно: иначе у выбранного чипа стоит его число, а у всех
 * соседних ноль, и переключиться становится некуда — фильтр выглядит сломанным.
 */
export function facet(records, state, dimension) {
  const rest = { ...state, [dimension]: "" };
  const counts = new Map();
  for (const record of applyFilters(records, rest)) {
    for (const value of valuesOf(record, dimension)) {
      counts.set(value, (counts.get(value) || 0) + 1);
    }
  }
  return counts;
}

function valuesOf(record, dimension) {
  if (dimension === "year") return [yearOf(record)].filter(Boolean);
  if (dimension === "category") return [record.category].filter(Boolean);
  if (dimension === "topic") return record.topics || [];
  if (dimension === "sub") return [record.subgroup].filter(Boolean);
  if (dimension === "tag") return record.tags || [];
  if (dimension === "speaker") return peopleOf(record);
  if (dimension === "kind") return record.kind || [];
  return [record.section].filter(Boolean);
}

/**
 * Вторая ось выбранного раздела — и нужна она НЕ ВСЕГДА.
 *
 * У митапов подраздел это год, и показывать его рядом со строкой годов значит показать одно и
 * то же дважды. У курса подраздел — сам курс, и вот он единственная осмысленная ось: год там
 * бесполезен (все лекции выложены одним днём) и вдобавок врёт.
 */
export function subAxis(records, state) {
  if (!state.section) return [];
  const values = [...facet(records, { ...state, sub: "" }, "sub").keys()];
  if (!values.length || values.every((v) => /^\d{4}$/.test(v))) return [];
  return values.sort(naturalCmp);
}

/** Состояние из адреса. Ключи латиницей: русские в ссылке превращаются в частокол процентов. */
export function fromQuery(search) {
  const params = new URLSearchParams(search || "");
  const state = { ...EMPTY };
  for (const key of Object.keys(EMPTY)) state[key] = params.get(key) || "";
  return state;
}

/** Адрес из состояния: пустые ключи не пишем, иначе ссылка обрастает мусором. */
export function toQuery(state) {
  const params = new URLSearchParams();
  for (const key of Object.keys(EMPTY)) {
    const value = (state[key] || "").trim();
    if (value) params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export const isEmpty = (state) => Object.keys(EMPTY).every((k) => !(state[k] || "").trim());
