// Авто-тема сессии: заголовок разговора, который BFF обобщил после первого ответа.
// Появляется один раз за сессию — через ASCII-заставку, дальше это обычная шапка.
import { el, reducedMotion } from "../ui/dom.js";
import { playTopicIntro } from "../ui/ascii.js";

const SPARK =
  '<svg class="spk" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
  '<path d="M12 2l1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8z"/>' +
  '<path d="M19 14l.7 2.1L22 17l-2.3.9L19 20l-.7-2.1L16 17l2.3-.9z" opacity=".6"/></svg>';

let node = null;
let intro = null; // идущая сцена: играет, пока тема считается

/**
 * Ставит полосу и ЗАПУСКАЕТ сцену в момент отправки вопроса.
 * Сцена крутится, пока считается тема, и завершается по её приходу — так
 * ожидание заполнено делом, а не таймером, и вёрстка потом не дёргается.
 */
export function reserveTopic(stream) {
  if (node || !stream) return;
  const art = el("pre", { class: "fx-art" });
  const kicker = el("span", { class: "topic-kicker", text: "Определяю тему…" });
  node = el(
    "header",
    { class: "topic reserved thinking" },
    el("div", { class: "fx", "aria-hidden": "true" }, art),
    el("div", { class: "topic-eyebrow", html: SPARK }, kicker)
  );
  stream.prepend(node);

  if (!reducedMotion()) {
    intro = playTopicIntro(art);
    if (intro?.kicker) kicker.textContent = intro.kicker;
  }
}

/** Тема не пришла (осечка LLM) — гасим сцену и освобождаем место. */
export function dropReservation() {
  intro?.stop();
  intro = null;
  if (node?.classList.contains("reserved")) {
    node.remove();
    node = null;
  }
}

/** Показывает тему в начале ленты. Повторные вызовы игнорируются: тема ставится один раз. */
export function showTopic(stream, { title, summary, records = [] }, { onOpenRecord } = {}) {
  if (!title) return;
  const reserved = node?.classList.contains("reserved") ? node : null;
  if (node && !reserved) return; // тема ставится один раз за сессию

  // место уже занято и сцена уже играет — переиспользуем их, чтобы ничего не сдвинулось
  const art = reserved?.querySelector(".fx-art") || el("pre", { class: "fx-art" });
  const fx = reserved?.querySelector(".fx") || el("div", { class: "fx", "aria-hidden": "true" }, art);
  const kicker =
    reserved?.querySelector(".topic-kicker") || el("span", { class: "topic-kicker", text: "Определяю тему…" });

  const heading = el("h2", { class: "topic-title", text: title });
  const gloss = el("p", { class: "topic-gloss", text: summary || "" });
  const eps = el("div", { class: "topic-eps" });
  if (records.length) {
    eps.append("записи");
    for (const id of records) {
      const chip = el("span", { text: id, title: "Открыть запись" });
      chip.addEventListener("click", () => onOpenRecord?.(id));
      eps.append(chip);
    }
  }

  if (reserved) {
    reserved.classList.remove("reserved");
    reserved.append(heading, gloss, eps); // шапка уже стоит — просто наполняем её
  } else {
    node = el(
      "header",
      { class: "topic thinking" },
      fx,
      el("div", { class: "topic-eyebrow", html: SPARK }, kicker),
      heading,
      gloss,
      eps
    );
  }

  // до появления заголовок скрыт: сначала играет сцена, потом «проявляется» тема
  const body = [heading, gloss, eps];
  for (const part of body) {
    part.style.opacity = "0";
    part.style.transform = "translateY(6px)";
  }
  if (!reserved) stream.prepend(node);

  // Держим СВОЙ элемент, а не модульную переменную: пока сцена доигрывает,
  // пользователь мог начать новый чат — тогда reveal снял бы «определяю»
  // с чужой, только что созданной полосы.
  const target = node;
  const reveal = () => {
    kicker.textContent = "Тема разговора";
    target.classList.remove("thinking");
    body.forEach((part, i) =>
      setTimeout(() => {
        part.style.transition = "opacity .5s, transform .5s";
        part.style.opacity = "1";
        part.style.transform = "none";
      }, i * 130)
    );
  };

  if (intro) {
    // сцена уже крутится с момента вопроса — просто говорим ей, что тема пришла
    intro.finish(reveal);
    intro = null;
  } else if (reducedMotion()) {
    reveal();
  } else {
    // сюда попадаем, если полосу не резервировали (например, восстановленный диалог)
    const started = playTopicIntro(art);
    if (started?.kicker) kicker.textContent = started.kicker;
    started?.finish(reveal);
  }
}

/** Показана ли уже тема — по этому флагу клиент решает, просить ли её у сервера. */
export function hasTopic() {
  // пустой резерв темой НЕ считается: иначе клиент решит, что тема уже есть,
  // и перестанет просить её у сервера (ловили — полоса оставалась пустой)
  return Boolean(node) && !node.classList.contains("reserved");
}

/** «Новый чат» — тема прошлой сессии уходит вместе с ней, вместе с её сценой. */
export function clearTopic() {
  intro?.stop(); // иначе доигрывающая сцена трогала бы уже чужую полосу
  intro = null;
  node?.remove();
  node = null;
}
