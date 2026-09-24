// Сцена 2: живые исправления. Модель предлагает пары «было → стало», код их проверяет и
// применяет — и здесь видно обе половины работы: что принято и что отвергнуто, с причиной.
//
// Отвергнутые интереснее принятых: по ним видно, что сторож не декоративный. У модели вообще не
// берут прозу — только пары, и каждая проходит семь проверок, поэтому правка физически не может
// выкинуть речь. Мы это показываем, а не рассказываем.
//
// ⚠️ Порядок карточек — ПО ЗАВЕРШЕНИЮ, а не по времени записи: реплики считаются параллельно
// (шесть разом) плюс добивочный проход. Поэтому у карточки стоит тайм-код, и подпись сцены об
// этом говорит — иначе человек решит, что запись обрабатывают задом наперёд.
// ⚠️ Узел — СЛОВО, а не буква: посимвольная анимация DOM в этом проекте уже признана неподъёмной.

import { clock, el } from "./dom.js";

const KEEP = 40;   // больше на экран не влезает, а список не лог: старое уезжает молча

// Причины отказа приходят из движка ключами — здесь единственное место, где они становятся
// человеческими словами. Незнакомый ключ показываем как есть: молчать хуже.
const WHY = {
  empty: "пусто или ничего не меняет",
  too_long: "слишком длинная — это уже не сущность",
  not_found: "в тексте нет по границам слова",
  number: "меняет число — его решает акустика",
  excision: "выбрасывает слова",
  translation: "перевод обычной речи",
  invented_name: "выдуманное имя",
  breaks_term: "сломало бы известный термин",
};

export function fixes(root) {
  const list = el("ul", { class: "fx-list" });
  const ticks = el("div", { class: "fx-ticks" });
  const count = el("p", { class: "fx-count" });
  root.append(ticks, list, count);

  let applied = 0;
  let dropped = 0;
  let turns = 0;
  let built = 0;

  function ensureTicks(n) {
    // ⚠️ Полоску реплик строим ОДИН раз и дальше только меняем классы: пересборка узлов на
    // каждое событие сбрасывала бы переходы и превращала полоску в мигание.
    if (n <= built) return;
    for (let i = built; i < n; i++) ticks.append(el("i", { class: "fx-tick" }));
    built = n;
  }

  function summary() {
    count.textContent = turns
      ? `принято ${applied} · отброшено ${dropped} · реплик ${turns}`
      : `принято ${applied} · отброшено ${dropped}`;
  }

  return {
    apply(e) {
      if (e.t === "stage.start" && e.stage === "final-round") {
        root.removeAttribute("hidden");
        return;
      }
      if (e.t === "turn.fix") {
        const ok = e.ok === true;
        // ⚠️ Зачёркнутым бывает то, чего БОЛЬШЕ НЕТ, и это разные половины у разных исходов:
        // приняли — исчезло старое слово, отвергли — не состоялось предложение модели. Поэтому
        // тег выбирается по исходу, а не правится потом стилем: `<s>` зачёркивает сам по себе,
        // и попытка «отменить» его классом читалась бы как ошибка вёрстки.
        const card = el("li", { class: "fx" },
          el("time", { text: clock(e.start) }),
          el(ok ? "s" : "span", { class: "fx-was", text: e.was }),
          el("span", { class: "fx-arrow", text: "→" }),
          el(ok ? "b" : "s", { class: "fx-now", text: e.now }));
        card.setAttribute("data-ok", ok ? "1" : "0");
        if (!ok) {
          const why = WHY[e.why] || e.why || "не принято";
          card.append(el("span", { class: "fx-why", text: e.term ? `${why} «${e.term}»` : why }));
        }
        list.prepend(card);
        while (list.children.length > KEEP) list.lastElementChild.remove();
        // Класс ставим СЛЕДУЮЩИМ кадром: переход живёт на входящем состоянии, поэтому
        // появление плавное, а удаление мгновенное (тот же приём, что у подсветки караоке).
        requestAnimationFrame(() => card.classList.add("land"));
        if (ok) applied += 1; else dropped += 1;
        summary();
        return;
      }
      if (e.t === "turn.done") {
        turns = e.n || turns;
        ensureTicks(turns);
        const tick = ticks.children[Math.max(0, (e.turn || 0))];
        if (tick) {
          tick.className = "fx-tick " + (e.failed ? "failed" : e.changed ? "changed" : "kept");
          tick.title = `${clock(e.start)} — ${e.failed ? "не долечилась" : e.changed ? "правлена" : "без правок"}`;
        }
        summary();
      }
    },
    reset() {
      applied = dropped = turns = built = 0;
      list.replaceChildren();
      ticks.replaceChildren();
      count.textContent = "";
      root.setAttribute("hidden", "");
    },
    state: () => ({ applied, dropped, turns, cards: list.children.length }),
  };
}
