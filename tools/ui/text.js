// Сцена текста: то, ради чего человек и ждёт. У неё два возраста, и это ОДНА сцена намеренно —
// сначала текст появляется, потом его правят на том же месте.
//
//   1) «пишется» (пасс-2): распознанный кусок печатается по мере появления. Печатаем ПО КАДРУ и
//      пачкой знаков, а не по одному в таймере: узел один, переписывается его текст — это дёшево,
//      в отличие от посимвольной анимации узлами, которая в этом проекте уже признана
//      неподъёмной. Текст при этом настоящий: он ровно в этот момент и распознан.
//   2) «правится» (финал-раунд): показывается реплика, над которой идёт работа, и слово меняется
//      ПРЯМО В НЕЙ — подсветка, потом замена. Принятая правка остаётся в тексте, отвергнутая
//      краснеет и исчезает, оставив исходное слово на месте.
//
// ⚠️ Правки приходят пачками (шесть реплик считаются разом), поэтому у сцены СВОЙ темп: замена
// показывается не быстрее, чем её можно прочитать. Отстали — ускоряемся, но не мгновенно.

import { clock, el, reduced } from "./dom.js";

const TYPE_MS = 900;       // за столько печатается кусок, если не торопимся
const FIX_HOLD = 420;      // сколько слово подсвечено до замены — время заметить глазом
const FIX_GAP = 260;       // пауза между правками
const KEEP_CHARS = 1400;   // сколько текста держим на экране в режиме «пишется»
const KEEP_CARDS = 30;

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

/** Границы слова — как у движка: замена внутри слова это не сущность, а порча. */
function wordRe(word) {
  return new RegExp(`(?<![\\w])${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w])`);
}

export function textScene(root) {
  const head = el("p", { class: "tx-head" });
  const body = el("p", { class: "tx-body" });
  const list = el("ul", { class: "fx-list" });
  const ticks = el("div", { class: "fx-ticks" });
  const count = el("p", { class: "fx-count" });
  root.append(head, body, ticks, list, count);

  let mode = "idle";          // idle | writing | fixing
  let plain = "";             // что уже показано (режим «пишется»)
  let pending = "";           // что ещё печатается
  let typed = 0;
  let applied = 0, dropped = 0, turns = 0, built = 0;
  const jobs = [];            // очередь правок этой сцены
  let busy = 0;               // до какого момента занята (мс)
  let raf = null;

  function pump() {
    if (raf === null) raf = requestAnimationFrame(tick);
  }

  function renderPlain() {
    body.replaceChildren(plain.slice(-KEEP_CHARS) + (pending ? pending.slice(0, typed) : ""));
  }

  function tick(now) {
    raf = null;
    let more = false;

    if (pending) {
      const step = reduced() ? pending.length : Math.max(1, Math.ceil(pending.length / (TYPE_MS / 16)));
      typed = Math.min(pending.length, typed + step);
      renderPlain();
      if (typed >= pending.length) { plain += pending; pending = ""; typed = 0; }
      else more = true;
    }

    if (jobs.length && now >= busy) {
      runFix(jobs.shift(), now);
      more = true;
    }
    if (jobs.length) more = true;
    if (more) pump();
  }

  // --- режим «правится» -------------------------------------------------------------------

  function showTurn(e) {
    mode = "fixing";
    head.textContent = `реплика ${clock(e.start)}${e.whole ? "" : " · кусок"}`;
    body.replaceChildren(e.text || "");
    body.className = "tx-body fixing";
  }

  function runFix(e, now) {
    const ok = e.ok === true;
    card(e, ok);
    const re = wordRe(e.was || "");
    const text = body.textContent;
    const hit = re.exec(text);
    if (!hit) {                       // замена вне показанного куска — честно только карточкой
      busy = now + FIX_GAP;
      return;
    }
    const mark = el("mark", { class: ok ? "tx-hit" : "tx-hit bad", text: hit[0] });
    body.replaceChildren(text.slice(0, hit.index), mark, text.slice(hit.index + hit[0].length));
    busy = now + FIX_HOLD + FIX_GAP;

    const swap = () => {
      if (ok) {
        // Принято — слово стало другим ПРЯМО В ТЕКСТЕ и таким и остаётся.
        mark.textContent = e.now;
        mark.className = "tx-hit done";
        setTimeout(() => {
          const t = body.textContent;                     // рассыпаем разметку обратно в текст
          body.replaceChildren(t);
        }, 600);
      } else {
        // Отвергнуто — старое слово ОСТАЁТСЯ. Показываем это: краснеет и гаснет.
        mark.className = "tx-hit bad shown";
        setTimeout(() => { mark.className = "tx-hit bad gone"; }, 700);
        setTimeout(() => { body.replaceChildren(body.textContent); }, 1100);
      }
    };
    if (reduced()) swap(); else setTimeout(swap, FIX_HOLD);
  }

  function card(e, ok) {
    const item = el("li", { class: "fx" },
      el("time", { text: clock(e.start) }),
      el(ok ? "s" : "span", { class: "fx-was", text: e.was }),
      el("span", { class: "fx-arrow", text: "→" }),
      el(ok ? "b" : "s", { class: "fx-now", text: e.now }));
    item.setAttribute("data-ok", ok ? "1" : "0");
    if (!ok) {
      const why = WHY[e.why] || e.why || "не принято";
      item.append(el("span", { class: "fx-why", text: e.term ? `${why} «${e.term}»` : why }));
    }
    list.prepend(item);
    while (list.children.length > KEEP_CARDS) list.lastElementChild.remove();
    requestAnimationFrame(() => item.classList.add("land"));
    if (ok) applied += 1; else dropped += 1;
    summary();
  }

  function ensureTicks(n) {
    // ⚠️ Полоска реплик строится ОДИН раз: пересборка узлов сбрасывала бы переходы.
    if (n <= built) return;
    for (let i = built; i < n; i++) ticks.append(el("i", { class: "fx-tick" }));
    built = n;
  }

  function summary() {
    count.textContent = applied || dropped
      ? `принято ${applied} · отброшено ${dropped}${turns ? ` · реплик ${turns}` : ""}` : "";
  }

  return {
    apply(e) {
      if (e.t === "stage.start" && e.stage === "pass2") {
        root.removeAttribute("hidden");   // сцена открывается уже на распознавании, не на правках
        mode = "writing";
        head.textContent = "распознаю — текст появляется по мере готовности";
        body.className = "tx-body";
        return;
      }
      if (e.t === "chunk.done" && mode === "writing" && e.raw) {
        // Текст ставим в очередь целиком, а печатаем по кадрам: событие пришло одно, а читается
        // оно секунду — это и есть «по мере появления», без выдумок.
        plain += pending.slice(0, typed);
        pending = (pending.slice(typed) + " " + e.raw).trim() + " ";
        typed = 0;
        pump();
        return;
      }
      if (e.t === "stage.start" && e.stage === "final-round") {
        root.removeAttribute("hidden");
        head.textContent = "правлю сущности";
        plain = pending = ""; typed = 0;
        return;
      }
      if (e.t === "turn.text") { showTurn(e); return; }
      if (e.t === "turn.fix") { jobs.push(e); pump(); return; }
      if (e.t === "turn.done") {
        turns = e.n || turns;
        ensureTicks(turns);
        const tick_ = ticks.children[Math.max(0, e.turn || 0)];
        if (tick_) {
          tick_.className = "fx-tick " + (e.failed ? "failed" : e.changed ? "changed" : "kept");
          tick_.title = `${clock(e.start)} — ${e.failed ? "не долечилась" : e.changed ? "правлена" : "без правок"}`;
        }
        summary();
      }
    },
    reset() {
      mode = "idle"; plain = pending = ""; typed = 0; jobs.length = 0; busy = 0;
      applied = dropped = turns = built = 0;
      head.textContent = ""; body.replaceChildren(); list.replaceChildren();
      ticks.replaceChildren(); count.textContent = "";
      root.setAttribute("hidden", "");
    },
    state: () => ({ mode, applied, dropped, turns, cards: list.children.length,
                    text: body.textContent, queued: jobs.length }),
  };
}
