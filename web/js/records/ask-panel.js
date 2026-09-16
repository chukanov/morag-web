// Вопрос К ЭТОЙ ЗАПИСИ — прямо на её странице: кнопки-пресеты, своё поле и лента ответов.
//
// Зачем отдельный прогон, а не контроллер чата: тот прибит к четырём узлам экрана диалога
// (`#stream`, `#form`, `#q`, `#send`), ведёт историю в localStorage и заказывает ТЕМУ разговора,
// а заставка темы — синглтон на документ. Здесь нужно другое: лента в своём контейнере, тема не
// нужна вовсе (`want_topic: false`), разговор живёт ровно пока открыта страница. Общего с чатом
// остаётся главное — рисование хода (`chat/view.js`) и разбор потока (`api.js` → `sse.js`).
//
// Цитаты — карточками-моментами, как в чате (владелец, 15.09: карточка с плеером и подсветкой
// значимого понятна, прыжок в расшифровку — нет). Второго `<video>` карточка не заводит: адрес
// цитаты своей записи равен адресу читалки, играет тот же элемент в кадре читалки — а второго
// ВЛАДЕЛЬЦА читалка разводит сама (`reader.js`). Цитата из чужой записи играет, как в чате, в
// мини-панели шапки; «открыть» — обычный переход. После ответа в блоке остаются только моменты
// со сноской в тексте (`pruneUnreferenced`): запись загружена целиком, и её чанки — это все.
import { el } from "../ui/dom.js";
import { ask, sendFeedback } from "../api.js";
import { createTurn } from "../chat/view.js";

/** Набор кнопок для ветки: своя строка заменяет общую целиком, «*» — общая. */
export function presetsFor(map, section) {
  const byBranch = map && typeof map === "object" ? map : {};
  const items = byBranch[section] || byBranch["*"] || [];
  return Array.isArray(items) ? items.filter((i) => i && i.label && i.question) : [];
}

const PLACEHOLDER = "Спросить про эту запись…";

/**
 * Панель вопросов к записи.
 * @param {{record: object, presets: object, onAskCorpus: Function,
 *          onOpenRecord: Function, onShareMoment: Function}} opts
 */
export function createAskPanel({ record, presets, onAskCorpus, onOpenRecord, onShareMoment }) {
  const buttons = presetsFor(presets, record.section).map((preset) => {
    const node = el("button", { class: "qa-preset", type: "button", text: preset.label });
    node.addEventListener("click", () => {
      scope.checked = true; // пресет по смыслу «спроси у этой записи» — галочку возвращаем
      run(preset.question);
    });
    return node;
  });
  const row = el("div", { class: "qa-presets" }, ...buttons);

  const input = el("input", { class: "qa-input", type: "text", placeholder: PLACEHOLDER,
                              "aria-label": PLACEHOLDER, autocomplete: "off" });
  const send = el("button", { class: "qa-send", type: "submit", text: "Спросить" });
  const scope = el("input", { type: "checkbox", checked: "" });
  const scopeLabel = el("label", { class: "qa-scope" }, scope, el("span", { text: "только у этой записи" }));
  const form = el("form", { class: "qa-form" }, input, send);
  const stream = el("div", { class: "qa-stream" });
  const node = el("section", { class: "rd-qa" }, row, form, scopeLabel, stream);
  if (!buttons.length) row.hidden = true;

  // История панели — в памяти страницы: разговор про запись кончается вместе с ней, в
  // localStorage ему не место (там диалоги корпуса, у них свои адреса).
  const history = [];
  const sessionId = `rec-${record.id}-${Date.now().toString(36)}`;
  const turns = [];
  let busy = false;
  let controller = null;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || busy) return;
    if (!scope.checked) {
      // Вопрос по всему корпусу отвечает экран диалога: там цитаты из чужих записей проверяют
      // карточкой со своим плеером — ровно тем, чего на странице с открытым видео быть не может.
      input.value = "";
      onAskCorpus?.(question);
      return;
    }
    run(question);
  });

  function setBusy(on) {
    busy = on;
    input.disabled = on;
    send.disabled = on;
    // ⚠️ Ряд гасим на время ответа: лимит посетителя — пять вопросов за две минуты и один
    // одновременный, а четыре кнопки подряд упёрлись бы в 429 быстрее, чем придёт первый ответ.
    for (const b of buttons) b.disabled = on;
    send.textContent = on ? "Отвечаю…" : "Спросить";
  }

  async function run(question) {
    if (busy) return;
    setBusy(true);
    input.value = "";
    const turn = createTurn(question, { onOpenRecord, onShareMoment, onFeedback: sendFeedback,
                                        pruneUnreferenced: true });
    turns.push(turn);
    stream.append(turn.node);
    turn.node.scrollIntoView({ block: "nearest" });

    const abort = new AbortController();
    controller = abort;
    const parts = [];
    let failed = false;
    let finished = false;
    try {
      for await (const frame of ask(
        { question, sessionId, history, context: { record_id: record.id, scope: "record" },
          wantTopic: false },
        abort.signal
      )) {
        switch (frame.type) {
          case "answer_id": turn.setAnswerId(frame.id); break;
          case "status": turn.addStatus(frame.text); break;
          case "token": parts.push(frame.text); turn.addToken(frame.text); break;
          case "citation": turn.addCitation(frame); break;
          case "done": finished = true; turn.finish(); break;
          case "error": failed = true; turn.fail(frame.message || "Что-то пошло не так."); break;
          default: break; // незнакомый кадр — не ломаемся
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        turn.node.remove();
        turns.splice(turns.indexOf(turn), 1);
        turn.dispose();
      } else {
        failed = true;
        turn.fail(turn.hasText()
          ? "Связь оборвалась — ответ показан не полностью."
          : "Связь с сервером прервалась. Попробуйте ещё раз.");
      }
    } finally {
      if (!failed && !finished) turn.finish();
      if (finished && !failed) {
        history.push({ role: "user", content: question });
        history.push({ role: "assistant", content: parts.join("") });
      }
      controller = null;
      setBusy(false);
    }
  }

  return {
    node,
    ask: run,
    /** Открыть панель под руку: галочка на месте, курсор в поле. */
    focus() {
      scope.checked = true;
      node.scrollIntoView({ block: "center" });
      input.focus({ preventScroll: true });
    },
    /** Уходим со страницы: оборвать ответ и снять подписки ходов на документе. */
    dispose() {
      controller?.abort();
      controller = null;
      for (const turn of turns) turn.dispose();
      turns.length = 0;
    },
  };
}
