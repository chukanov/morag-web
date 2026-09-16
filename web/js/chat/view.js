// Один ход диалога: вопрос → лента «как искал» → ответ → моменты → действия.
import { el, splitIcon, toast } from "../ui/dom.js";
import { momentsBlock } from "./moments-block.js";
import { renderMarkdown, claimsByRef } from "./md.js";

const CHEV =
  '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';
// Индикатор поиска — значок из самого статуса движка («🔍», «📄», «✅»):
// он и так говорит, что происходит, поэтому свой знак здесь был бы лишним.
const UP =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 11v9M14 4l-2 7h6.5a2 2 0 0 1 2 2.4l-1.3 6A2 2 0 0 1 17.2 21H7v-10z"/></svg>';
const DOWN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 13V4M10 20l2-7H5.5a2 2 0 0 1-2-2.4l1.3-6A2 2 0 0 1 6.8 3H17v10z"/></svg>';
const BOOK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5V6a2 2 0 0 1 2-2h13v16H6.5A2.5 2.5 0 0 0 4 22.5z"/></svg>';

/**
 * Один ход диалога. Узел контейнер-агностичен: вставляется и в ленту чата, и в панель на
 * странице записи — с теми же карточками-моментами (владелец, 15.09: цитата понятна
 * карточкой с плеером и подсветкой значимого; прыжок в расшифровку понятен не был).
 *
 * ⓘ Второго `<video>` карточка на странице записи не заводит: адрес цитаты своей записи
 * совпадает с адресом читалки байт в байт, играет тот же единственный элемент в том же кадре.
 * Появляется второй ВЛАДЕЛЕЦ (`mN` против `rd:`), и всё, что читалка решает по владельцу, —
 * липкость, караоке, кнопка пуска — разведено в `records/reader.js`, не здесь.
 *
 * `pruneUnreferenced` — после ответа оставить только моменты со сноской в тексте: на странице
 * записи движок отдаёт ВСЕ её чанки (запись загружена целиком), и сводка «96 моментов» на
 * странице той же записи ничего не говорит.
 */
export function createTurn(question, { context = null, onOpenRecord, onShareMoment, onFeedback,
                                       pruneUnreferenced = false, restored = false } = {}) {
  const ask = el("div", { class: "ask" });
  if (context) {
    const chip = el(
      "button",
      { class: "ctx-chip", html: BOOK, title: "Вернуться к этому месту" },
      el("span", { class: "q", text: `${context.label} · ${context.tc} · ${context.speaker}` })
    );
    chip.addEventListener("click", () => onOpenRecord?.(context.record_id, context.sec));
    ask.append(chip);
  }
  ask.append(el("div", { class: "bubble", text: question }));

  // Лента «как искал»: свёрнутая показывает ПОСЛЕДНИЙ шаг, раскрытая — все.
  // Отдельной итоговой строки нет: свежий статус и есть итог на данный момент.
  const sumMeta = el("span", { class: "sum-meta" });
  const chevron = el("span", { class: "chev", html: CHEV });
  const steps = el("div", { class: "steps" });
  const trace = el("div", {
    class: "trace live",
    role: "button",
    tabindex: "0",
    "aria-label": "Показать, как искал",
  });
  trace.append(steps);
  addStep("🧭", "Думаю…").dataset.seed = "1"; // временная строка до первого статуса движка

  const toggle = () => trace.classList.toggle("open");
  trace.addEventListener("click", toggle);
  trace.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });

  /** Новый шаг; время и «галочку» переносим на него — они всегда на последней строке. */
  function addStep(icon, text) {
    const row = el(
      "div",
      { class: "step" },
      el("span", { class: "st-ico", text: icon }),
      el("span", { class: "st-text", text })
    );
    steps.append(row);
    row.append(sumMeta, chevron);
    return row;
  }

  const answer = el("div", { class: "answer" });
  const caret = el("span", { class: "caret" });
  answer.append(el("p", {}, caret));

  // У восстановленного хода ленты «как искал» нет: статусы мы не храним —
  // они про то, как шёл поиск, а не про сам ответ.
  const reply = restored ? el("div", { class: "reply" }, answer) : el("div", { class: "reply" }, trace, answer);
  const turn = el("section", { class: "turn enter" }, ask, reply);
  if (restored) caret.remove();

  let moments = null; // блок моментов заводим только когда цитаты действительно пришли
  const refNodes = new Map(); // n → узел сноски в тексте: дорога от карточки обратно
  const claimNodes = new Map(); // n → фразы ответа с этой сноской: их и подсвечиваем

  // Врезка: место, куда карточка-момент переезжает прямо в текст ответа.
  //
  // Так проверка цитаты не уводит взгляд вниз и не теряет место чтения —
  // текст раздвигается там, где стоит сноска. Узел один на весь ход: открыть
  // сразу две цитаты нельзя, да и незачем.
  // Узлы — span, а не div: врезка встаёт ВНУТРИ абзаца, сразу за предложением,
  // а блочный элемент внутри `<p>` невалиден. Блочную раскладку даёт CSS.
  const slotBody = el("span", { class: "slot-body" });
  const slot = el("span", { class: "slot" }, slotBody);
  let openAt = 0; // номер раскрытой цитаты (0 — закрыто)

  // Клик мимо врезки закрывает её: раскрытая цитата — состояние «сейчас
  // проверяю», и выходить из него человек должен не глядя, куда жать.
  //
  // ⚠️ Подписка стоит ДО `return` намеренно. Всё, что ниже него, не выполняется
  // никогда: функции всплывают, а `const` и вызовы — нет. В этом файле грабля
  // уже описана, и я в неё всё равно вступил — слушатели молча не вешались.
  // ⚠️ Подписки снимаются в `dispose()`: ходов за сессию десятки (а на странице записи они ещё
  // и живут рядом с читалкой), и вечные слушатели на документе копились бы молча.
  const offs = [];
  const onDoc = (type, fn) => {
    document.addEventListener(type, fn);
    offs.push(() => document.removeEventListener(type, fn));
  };
  onDoc("click", (event) => {
    if (!openAt) return;
    if (slot.contains(event.target) || event.target.closest?.(".ref")) return;
    closeSlot();
  });
  onDoc("keydown", (event) => {
    if (event.key === "Escape") closeSlot();
  });
  const started = Date.now();
  let answerId = null;
  const citations = [];
  const rawParts = [];   // сырой текст ответа: по нему собираем разметку в конце

  // ВАЖНО: объявляем ДО return. Объявления после него не выполняются никогда —
  // функции всплывают, а let навсегда остаётся в «мёртвой зоне» (уже ловили).
  let renderTimer = null;
  let lastRender = 0;
  const RENDER_EVERY = 140; // пересобираем разметку ~7 раз в секунду

  return {
    node: turn,
    setAnswerId(id) {
      answerId = id;
    },
    addStatus(text) {
      const [icon, rest] = splitIcon(text);
      if (steps.children.length === 1 && steps.firstChild.dataset.seed) {
        steps.firstChild.remove(); // «Думаю…» уступает первому настоящему статусу
      }
      addStep(icon || "·", rest || text);
    },
    addToken(text) {
      rawParts.push(text);
      scheduleRender(); // разметку пересобираем по ходу печати, а не только в конце
    },
    addCitation(c) {
      citations.push(c);
      if (!moments) {
        moments = momentsBlock({ onOpenRecord, onShareMoment });
        reply.append(moments.node);
      }
      moments.add(c);
    },
    /** Снять подписки на документе и карточек на плеере. Зовётся при уходе со страницы. */
    dispose() {
      closeSlot();
      for (const off of offs) off();
      offs.length = 0;
      moments?.dispose();
    },
    finish() {
      caret.remove();
      trace.classList.remove("live");
      // только время: число шагов движок и так называет в своём последнем статусе
      sumMeta.innerHTML = `${((Date.now() - started) / 1000).toFixed(1)}&nbsp;с`;
      renderAnswer();
      // Чем цитата подпирает ответ, видно только когда ответ дописан: цитаты
      // приходят от движка РАНЬШЕ текста, и раздавать утверждения по ходу
      // печати означало бы раздавать половину предложения.
      moments?.setClaims(claimsByRef(rawParts.join("")), refNodes);
      if (pruneUnreferenced) moments?.keepOnly(refNodes.keys());
      reply.append(actions());
    },
    /** Восстановление из сохранённого диалога: текст и моменты уже известны. */
    restore({ answer: text, citations: saved = [] }) {
      rawParts.push(text || "");
      for (const c of saved) this.addCitation(c);
      renderAnswer();
      moments?.setClaims(claimsByRef(text || ""), refNodes);
      if (pruneUnreferenced) moments?.keepOnly(refNodes.keys());
      reply.append(actions());
    },
    fail(message) {
      caret.remove();
      trace.classList.remove("live");
      addStep("⚠️", "Не получилось");
      // если что-то уже напечатано — сохраняем: обрыв не повод выбрасывать ответ
      if (rawParts.length) {
        renderAnswer();
        reply.append(actions());
      }
      answer.append(el("div", { class: "nosrc", text: message }));
    },
    /** Успел ли ответ хоть что-то сказать (для выбора сообщения об ошибке). */
    hasText() {
      return rawParts.join("").trim().length > 0;
    },
  };

  // Ответ ВСЕГДА пересобираем из СЫРОГО текста: модель отдаёт markdown и режет
  // маркеры на куски («[», «1», «].»), так что разбирать готовый DOM бесполезно.
  // Во время печати делаем это не чаще ~7 раз в секунду — иначе лишняя работа.
  function scheduleRender() {
    if (renderTimer) return;
    const wait = Math.max(0, RENDER_EVERY - (Date.now() - lastRender));
    renderTimer = setTimeout(() => {
      renderTimer = null;
      lastRender = Date.now();
      renderAnswer({ streaming: true });
    }, wait);
  }

  function renderAnswer({ streaming = false } = {}) {
    if (renderTimer && !streaming) {
      clearTimeout(renderTimer);
      renderTimer = null;
    }
    let text = rawParts.join("");
    if (streaming) text = trimIncomplete(text);
    if (!text.trim()) return;
    refNodes.clear(); // разметка пересобирается целиком — прежние узлы уже не в документе
    claimNodes.clear();
    answer.replaceChildren(
      renderMarkdown(text, {
        makeRef: (n) => (moments?.has(n) ? refLink(n) : null),
        onClaim: (n, node) => claimNodes.set(n, [...(claimNodes.get(n) || []), node]),
      })
    );
    if (streaming) answer.lastElementChild?.append(caret); // курсор бежит за текстом
  }

  /** Недописанные маркеры не показываем «звёздочками»: они вот-вот закроются. */
  function trimIncomplete(text) {
    const tail = text.slice(-40);
    const open = (tail.match(/\*\*/g) || []).length;
    let cut = text;
    if (open % 2 === 1) cut = cut.slice(0, cut.lastIndexOf("**"));
    return cut.replace(/(?:\*|`|\[)\s*$/, "");
  }

  function refLink(n) {
    const ref = el("a", { class: "ref", href: `#m-${n}`, text: String(n) });
    refNodes.set(n, ref); // по нему карточка вернёт человека в текст ответа
    ref.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation(); // клик по сноске — не «клик мимо врезки»
      openSlot(n, ref);
    });
    return ref;
  }

  /**
   * Раскрыть цитату прямо в тексте: текст раздвигается сразу под абзацем.
   *
   * Карточку не пересоздаём, а ПЕРЕНОСИМ из списка моментов: у неё своя
   * подписка на звук, загруженные слова и караоке — пересборка стоила бы
   * повторной загрузки и потери проигрывания. Перенос узла всё это сохраняет.
   */
  function openSlot(n, ref) {
    if (!moments?.has(n)) return;
    if (openAt === n) return closeSlot(); // повторный клик по той же сноске закрывает
    closeSlot();

    const card = moments.detach(n);
    if (!card) return;
    slotBody.replaceChildren(card);
    // Сразу после предложения со сноской — она всегда в его конце, поэтому
    // текст расступается ровно там, где человек читал. Абзац как целое —
    // запасной вариант: сноска может стоять и вне размеченной фразы.
    (claimNodes.get(n)?.[0] || blockOf(ref)).after(slot);
    highlightClaim(n, true);
    openAt = n;
    // Раскрытие анимируем на следующем кадре: класс, поставленный в том же
    // кадре, что и вставка узла, браузер применяет без перехода.
    requestAnimationFrame(() => {
      slot.classList.add("open");
      // Внутри врезки взгляд ведём к первому важному месту — тому самому, что
      // подпирает подсвеченную фразу. Звук при этом не запускаем: раскрытие —
      // ещё не просьба слушать.
      moments.focusCard(n);
    });
  }

  function closeSlot() {
    if (!openAt) return;
    highlightClaim(openAt, false);
    moments?.attachBack(openAt);
    openAt = 0;
    slot.classList.remove("open");
    slot.remove();
    slotBody.replaceChildren();
  }

  /**
   * Блок ответа, ПОСЛЕ которого встаёт врезка, когда предложения не нашлось.
   *
   * Поднимаемся по дереву сами, без `closest` с селектором: так же надёжно, но
   * не зависит от того, насколько полно реализованы селекторы вокруг.
   */
  function blockOf(node) {
    const stop = new Set(["P", "H3", "H4", "UL", "OL", "BLOCKQUOTE"]);
    let cur = node;
    while (cur && cur.parentElement && cur.parentElement !== answer) cur = cur.parentElement;
    if (cur && !stop.has(cur.tagName) && cur.parentElement === answer) return cur;
    return cur || answer.lastElementChild;
  }

  /** Подсветить фразу, ради которой цитату и открыли. */
  function highlightClaim(n, on) {
    for (const node of claimNodes.get(n) || []) node.classList.toggle("claim-live", on);
  }

  function actions() {
    // сохранение вынесено на весь диалог (кнопка в шапке): ответ редко нужен в отрыве
    const up = el("button", { class: "act up", html: UP, title: "Хороший ответ" });
    const down = el("button", { class: "act down", html: DOWN, title: "Плохой ответ" });

    up.addEventListener("click", () => {
      down.classList.remove("on");
      up.classList.toggle("on");
      if (up.classList.contains("on")) {
        onFeedback?.(answerId, "up");
        toast("Спасибо за оценку 👍");
      }
    });
    down.addEventListener("click", () => {
      up.classList.remove("on");
      down.classList.toggle("on");
      if (down.classList.contains("on")) {
        onFeedback?.(answerId, "down");
        toast("Учли 👎 — станет материалом для улучшения");
      }
    });
    return el("div", { class: "ans-actions" }, up, down);
  }
}
