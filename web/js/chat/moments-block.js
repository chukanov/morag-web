// Блок моментов под ответом: свёрнутая сводка + карточки по требованию.
//
// Карточки раскрыты по умолчанию занимали пол-экрана и оттесняли сам ответ —
// а цитат в ответе бывает под три десятка. Поэтому основной вид — сводка, и она
// НЕ «12 моментов ⌄»: она отвечает на вопросы, ради которых в неё смотрят —
// сколько нашлось, из каких записей, про что и на каких минутах.
//
// Полная карточка (плеер, волна, расшифровка с караоке) открывается по клику на
// тайм-код — и ровно одна. Промежуточного «списка свёрнутых карточек» нет
// намеренно: сводка уже перечисляет всё компактнее, а второй список тех же
// цитат строкой ниже — это просто шум.
//
// Побочная выгода: карточка тянет свои слова для караоке только при раскрытии,
// и свёрнутый блок экономит сотни килобайт на мобильном.
import { el, fmt, countOf } from "../ui/dom.js";
import { momentCard } from "./moments.js";

const CHEV =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';

/** «Капитанский мостик №2-9: с 8 марта · Anthropic» → «с 8 марта · Anthropic». */
/** Заголовок доклада из метки движка «Заголовок · 12:34 · Спикеры». */
function titleOf(label = "") {
  const parts = label.split(" · ");
  if (parts.length > 2) parts.pop(); // спикеры
  if (parts.length > 1) parts.pop(); // тайм-код
  return parts.join(" · ");
}

export function momentsBlock({ onOpenRecord, onShareMoment } = {}) {
  const cards = new Map(); // n → {card, expand, collapse}
  const groups = new Map(); // запись → {row, times, topics}
  const citations = [];

  const count = el("span", { class: "mo-count" });
  const meta = el("span", { class: "mo-meta" });
  const chev = el("span", { class: "mo-chev", html: CHEV });
  const head = el("button", { class: "mo-head", "aria-expanded": "false" }, count, meta, chev);
  const groupsBox = el("div", { class: "mo-groups" });
  // Самый плотный вид: только бейджи записей в строку. Нужен, когда моментов
  // много и они оттесняют сам ответ, но и тогда должно быть видно, ИЗ ЧЕГО
  // ответ собран — иначе это просто «свёрнуто» без всякой пользы.
  const badgesBox = el("div", { class: "mo-badges" });
  // Карточки живут здесь всегда, но видна только раскрытая (остальные скрыты
  // стилями). Держим их в DOM, а не пересоздаём: у карточки своя подписка на
  // звук и уже загруженные слова караоке.
  const cardsBox = el("div", { class: "mo-cards" });
  const node = el("div", { class: "moments mo-compact" }, head, badgesBox, groupsBox, cardsBox);

  // Плотный вид — состояние по умолчанию: ответ важнее списка цитат, а из чего
  // он собран, видно и по бейджам.
  let compact = true;
  function setCompact(next) {
    compact = next;
    node.classList.toggle("mo-compact", compact);
    head.setAttribute("aria-expanded", String(!compact));
    // Схлопнули всё — закрываем и раскрытую карточку: иначе она осталась бы
    // висеть под строкой бейджей, и «свернул» означало бы «почти свернул».
    if (compact) for (const entry of cards.values()) entry.collapse();
  }
  head.addEventListener("click", () => setCompact(!compact));
  // В плотном виде разворачивает клик по любому месту — включая бейджи записей.
  // Мелкая шапка как единственная мишень плохо попадается пальцем, а бейджи
  // выглядят кликабельными и обязаны что-то делать.
  badgesBox.addEventListener("click", () => setCompact(false));

  /**
   * Показать конкретный момент: раскрываем карточку и ведём к ней взгляд.
   *
   * `from` — узел сноски, по которой пришли. Карточка покажет по нему дорогу
   * назад: цитату открывают, чтобы проверить утверждение ответа, и без возврата
   * человек теряет то самое место, ради которого всё затевалось.
   */
  function reveal(n, { play = false, from = null } = {}) {
    const entry = cards.get(n);
    if (!entry) return;
    setCompact(false); // пришли из текста ответа — показываем, откуда цитата
    for (const [key, other] of cards) if (key !== n) other.collapse();
    entry.expand({ play, from });
    entry.card.scrollIntoView({ behavior: "smooth", block: "center" });
    entry.card.classList.remove("flash");
    void entry.card.offsetWidth; // перезапуск анимации: без этого второй клик её не покажет
    entry.card.classList.add("flash");
  }

  /** Тайм-код момента в строке его записи (строка заводится при первом моменте записи). */
  function placeInSummary(c) {
    // Строка записи заводится один раз, дальше в неё капают тайм-коды: так
    // видно, что «пять цитат» — это две записи, а не пять разных разговоров.
    const key = c.rec || "—";
    let group = groups.get(key);
    if (!group) {
      // У подкаста здесь стоял номер выпуска («№2-9»). Идентификатор записи — это дата
      // и слаг («2026-03-12-kafka»), человеку он ничего не говорит, поэтому и в строке,
      // и в бейдже стоит заголовок доклада: он опознаётся и кликается в читалку.
      const name = titleOf(c.label) || c.rec || "—";
      const tag = el("span", { class: "mo-rec", text: name });
      const times = el("span", { class: "mo-times" });
      group = { row: el("div", { class: "mo-grp" }, tag, times), times };
      groups.set(key, group);
      groupsBox.append(group.row);
      badgesBox.append(el("span", { class: "mo-badge", text: name }));
      if (c.rec) {
        tag.addEventListener("click", (event) => {
          event.stopPropagation();
          onOpenRecord?.(c.rec, c.sec);
        });
      }
    }
    const at = el("button", {
      class: "mo-at",
      text: fmt(c.sec || 0),
      title: "Показать этот момент",
    });
    at.addEventListener("click", () => reveal(c.n, { play: true }));
    group.times.append(at);
  }

  function refresh() {
    count.textContent = countOf(citations.length, "момент", "момента", "моментов");
    const records = groups.size;
    const bits = [];
    if (records > 1) bits.push(`из ${countOf(records, "записи", "записей", "записей")}`);
    // Сколько это слушать — считаем по промежуткам чанков, если сервер их дал.
    const span = citations.reduce((sum, c) => sum + Math.max(0, (c.end ?? c.sec) - c.sec), 0);
    if (span > 60) bits.push(`≈${Math.round(span / 60)} мин аудио`);
    meta.textContent = bits.join(" · ");
  }

  return {
    node,
    has: (n) => cards.has(n),
    reveal,

    add(c) {
      citations.push(c);

      // Аккордеон: раскрыт ровно один момент. Иначе в списке оказывается
      // несколько плееров сразу — непонятно, который звучит, и блок снова
      // разрастается на пол-экрана, ради чего всё и сворачивали.
      const { card, expand, collapse, setClaim, focus, dispose } = momentCard(c, {
        onOpenRecord,
        onShareMoment,
        onOpen: (n) => {
          for (const [key, other] of cards) if (key !== n) other.collapse();
        },
      });
      cards.set(c.n, { card, expand, collapse, setClaim, focus, dispose });
      cardsBox.append(card);
      placeInSummary(c);
      refresh();
    },

    /**
     * Оставить только моменты с номерами из `ns` — те, на которые в ответе есть сноска.
     *
     * Нужно на странице записи: движок отдаёт кадрами ВСЕ найденные фрагменты, а вопрос к
     * записи, загруженной целиком, находит все её чанки — «96 моментов из 1 записи» на странице
     * этой же записи ничего не говорит. Пусто в `ns` (агент не поставил ни одной сноски) —
     * не трогаем: показать найденное честнее, чем спрятать всё.
     */
    keepOnly(ns) {
      const keep = new Set(ns);
      if (!keep.size) return;
      for (const [n, entry] of [...cards]) {
        if (keep.has(n)) continue;
        entry.dispose();
        entry.card.remove();
        cards.delete(n);
      }
      citations.splice(0, citations.length, ...citations.filter((c) => keep.has(c.n)));
      // Сводку и бейджи собираем заново по оставшимся: строка записи с тайм-кодами удалённых
      // моментов вела бы в карточки, которых больше нет.
      groups.clear();
      groupsBox.replaceChildren();
      badgesBox.replaceChildren();
      for (const c of citations) placeInSummary(c);
      refresh();
    },

    /** Уход хода: снять подписки карточек на плеер — иначе они слушают его до перезагрузки. */
    dispose() {
      for (const entry of cards.values()) entry.dispose();
    },

    /**
     * Отдать карточку наружу — она переезжает во врезку прямо в тексте ответа.
     *
     * Именно ПЕРЕДАЁМ узел, а не делаем копию: у карточки своя подписка на звук,
     * загруженные слова и караоке. Копия означала бы второй плеер и повторную
     * загрузку, а `append` в другом месте DOM просто переносит узел со всем его
     * состоянием.
     */
    detach(n) {
      const entry = cards.get(n);
      if (!entry) return null;
      for (const [key, other] of cards) if (key !== n) other.collapse();
      entry.expand({ from: null });
      entry.detached = true;
      return entry.card;
    },

    /** Подвести расшифровку к первому важному месту (звук не трогаем). */
    focusCard(n) {
      cards.get(n)?.focus?.();
    },

    /** Вернуть карточку в список и свернуть: проверка закончена. */
    attachBack(n) {
      const entry = cards.get(n);
      if (!entry?.detached) return;
      entry.collapse();
      cardsBox.append(entry.card);
      entry.detached = false;
    },

    /**
     * Раздать карточкам утверждения ответа, которые они подпирают.
     *
     * Приходит одним махом, когда ответ дописан: цитаты движок присылает
     * РАНЬШЕ текста, и по ходу печати утверждение было бы обрывком фразы.
     */
    setClaims(claims, refs) {
      for (const [n, entry] of cards) entry.setClaim(claims?.get(n) || [], refs?.get(n) || null);
    },

    collapseAll() {
      for (const entry of cards.values()) entry.collapse();
    },
  };
}
