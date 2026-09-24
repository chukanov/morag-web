// Сцена текста — главное в окне. Это ОДИН документ, который сначала пишется, а потом правится
// на месте; по нему можно листать назад, как по расшифровке.
//
//   1) «пишется» (пасс-2): распознанный кусок печатается по мере появления. Печатаем ПО КАДРУ и
//      пачкой знаков, а не по знаку в таймере: узел один, переписывается его текст — это дёшево,
//      в отличие от посимвольной анимации узлами, которая в этом проекте признана неподъёмной.
//      Текст настоящий: он ровно в этот момент и распознан, мы лишь показываем его со скоростью
//      чтения.
//   2) «правится» (финал-раунд): слово подсвечивается ТЕМ ЖЕ единственным способом, что и всегда,
//      зачёркивается — и сменяется заменой. В тексте остаётся ИСПРАВЛЕННОЕ слово,
//      написанное оранжевым; прежнее уходит в слой над текстом и всплывает по наведению
//      вместе с причиной, если она есть. Конец работы — готовая расшифровка, а не лист корректуры.
//
// ⚠️ Подсветка ОДНА на всю сцену. Разные анимации на разные случаи читаются как рябь: глаз ищет
// правило и не находит. Правило здесь простое — жёлтым отмечено то, над чем работают ПРЯМО СЕЙЧАС.
// ⚠️ Правки приходят пачками (шесть реплик считаются разом), поэтому у сцены свой темп: замена
// показывается не быстрее, чем её можно прочитать.
// ⚠️ Прокрутка — МГНОВЕННАЯ. Плавная в браузере владельца не работает вовсе (замерено 08.09), и
// показ, зависящий от неё, просто стоял бы на месте.

import { clock, el, reduced } from "./dom.js";

const TYPE_MS = 900;       // за столько печатается кусок, если не торопимся
const HOLD_MS = 460;       // сколько слово подсвечено до замены — время заметить глазом
const GAP_MS = 240;        // пауза между правками
const HELD_MS = 4000;      // человек листает сам — столько за ним не бежим
const FRESH_MS = 1600;     // сколько прежнее слово видно само, без курсора
const CUT_MS = 340;        // сколько слово зачёркнуто до того, как его сменит замена

export const WHY = {
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
export function wordRe(word) {
  return new RegExp(`(?<![\\w])${String(word).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w])`);
}

export function textScene(root) {
  const head = el("p", { class: "tx-head" });
  const body = el("div", { class: "tx-body" });
  const count = el("p", { class: "fx-count" });
  root.append(head, body, count);

  let mode = "idle";
  let pending = "";          // что печатается сейчас
  let typed = 0;
  let marks = [];            // узлы уже поставленной корректуры — их не трогаем
  let tail = null;           // текстовый узел, в который печатаем
  let anchors = [];          // [[секунда, узел]] — куда листать по времени реплики
  let applied = 0, dropped = 0, turns = 0;
  const jobs = [];
  let busy = 0;
  let raf = null;
  let heldUntil = 0;

  // ⚠️⚠️ «Человек листает сам» берём из ЕГО ЖЕСТОВ, а не из события `scroll`. Событие поднимает
  // и наша собственная прокрутка, и сам браузер — подстановка корректуры меняет высоту строки
  // (надпись над ней), и он подтягивает scrollTop сам. Пока признаком считался `scroll`,
  // слежение выключалось САМО себя после первой же замены, и корректура ложилась за краем окна
  // (замерено на стенде: 2 корректуры в тексте, ни одной в видимой части). Тот же приём, что
  // в читалке сайта (`web/js/records/reader.js`), и по той же причине.
  for (const type of ["wheel", "touchmove", "keydown"]) {
    body.addEventListener(type, () => { heldUntil = performance.now() + HELD_MS; }, { passive: true });
  }

  function pump() { if (raf === null) raf = requestAnimationFrame(tick); }

  /** Смещение узла ВНУТРИ окна текста.
   *
   * ⚠️ Меряем ПРЯМОУГОЛЬНИКАМИ, а не `offsetTop`: тот считается от ближайшего
   * позиционированного предка, а окно текста позиционировано не всегда — тогда к смещению
   * приплюсовывалась вся шапка страницы, прокрутка улетала в конец и корректура оставалась за
   * кадром. Разница прямоугольников не зависит от вёрстки вокруг; `offsetTop` остаётся запасным
   * путём для узлов без геометрии (тесты).
   */
  function offsetIn(node) {
    if (typeof node.getBoundingClientRect !== "function"
        || typeof body.getBoundingClientRect !== "function") return node.offsetTop || 0;
    return node.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
  }

  /** Пока текст ПИШЕТСЯ, окно держится КОНЦА, а не начала последнего куска.
   *
   * ⚠️ Прежде слежение выводило на середину окна НАЧАЛО куска — а кусок бывает в несколько
   * строк, и самое свежее (то, ради чего смотрят) оказывалось ниже края. Здесь нужно
   * поведение ленты: новое приходит снизу, окно его догоняет.
   */
  function toEnd() {
    if (performance.now() < heldUntil) return;
    body.scrollTop = body.scrollHeight;
  }

  function follow(node) {
    // ⚠️ Без behavior:"smooth" — см. шапку файла.
    if (!node || performance.now() < heldUntil) return;
    body.scrollTop = Math.max(0, offsetIn(node) - body.clientHeight * 0.45);
  }

  /** Темп показа правок.
   *
   * ⚠️ Финал-раунд считает реплики ПАЧКАМИ (шесть разом), и при ровном темпе очередь
   * растёт, а окно показывает то, что было минуту назад. Окно показывает работу СЕЙЧАС — значит,
   * чем длиннее очередь, тем короче пауза; при пустой очереди замена показана не быстрее,
   * чем её можно прочитать.
   */
  function pace(queued) {
    const k = queued > 24 ? 0.25 : queued > 8 ? 0.5 : 1;
    return { hold: Math.round(HOLD_MS * k), gap: Math.round(GAP_MS * k) };
  }

  function tick(now) {
    raf = null;
    let more = false;
    if (pending) {
      const step = reduced() ? pending.length
                             : Math.max(1, Math.ceil(pending.length / (TYPE_MS / 16)));
      typed = Math.min(pending.length, typed + step);
      if (tail) tail.textContent = pending.slice(0, typed);
      if (typed >= pending.length) { pending = ""; typed = 0; tail = null; }
      else more = true;
      toEnd();
    }
    if (jobs.length && now >= busy) { runFix(jobs.shift(), now); more = true; }
    if (jobs.length) more = true;
    if (more) pump();
  }

  // --- текст пишется ---------------------------------------------------------------------

  function append(sec, raw) {
    // ⚠️ Прежний кусок дописываем ЦЕЛИКОМ, а не по напечатанному: иначе при быстром потоке
    // (или перемотке на стенде) каждый новый кусок обрезал бы предыдущий на полуслове, и от
    // текста оставались бы огрызки. Печать — это скорость показа, а не содержимое.
    finishTyping();
    const piece = el("span", { class: "tx-piece" });
    body.append(piece);
    anchors.push([sec, piece]);
    tail = piece;
    pending = (raw || "").trim() + " ";
    typed = 0;
    toEnd();          // кусок уже в разметке — показываем конец сразу, а не со следующего кадра
    pump();
  }

  // --- текст правится --------------------------------------------------------------------

  /** Узел, ближе всего стоящий к этой секунде записи: по нему ищем слово и туда листаем. */
  function near(sec) {
    let best = null;
    for (const [at, node] of anchors) {
      if (at <= sec + 1) best = node;
      else break;
    }
    return best;
  }

  /** Найти слово в тексте: сначала рядом с репликой, потом где угодно. */
  function locate(word) {
    const re = wordRe(word);
    const pieces = [...body.children].filter((n) => n.classList.contains("tx-piece"));
    const from = near(current);
    const order = from ? [from, ...pieces.filter((p) => p !== from)] : pieces;
    for (const piece of order) {
      for (const node of [...piece.childNodes]) {
        if (node.nodeType !== 3) continue;                   // текстовые узлы; корректуру не трогаем
        const hit = re.exec(node.textContent);
        if (hit) return { node, index: hit.index, word: hit[0], piece };
      }
    }
    return null;
  }

  let current = 0;

  function finishTyping() {
    // ⚠️ Правка ищет слово в готовом тексте. Если в этот момент что-то ещё печатается, слово может
    // быть «ещё не напечатано» — и правка молча не найдёт его. Дописываем немедленно: текст и так
    // весь пришёл, печать — только скорость показа.
    if (!pending) return;
    if (tail) tail.textContent = pending;
    pending = ""; typed = 0; tail = null;
  }

  function runFix(e, now) {
    finishTyping();
    const ok = e.ok === true;
    if (ok) applied += 1; else dropped += 1;
    summary();
    const { hold, gap } = pace(jobs.length);
    const spot = locate(e.was || "");
    if (!spot) { busy = now + gap; return; }                   // вне показанного — честно молчим

    // Разрезаем текстовый узел и ставим на слово ОДНУ подсветку — ту же, что и всегда.
    const rest = spot.node.splitText(spot.index);
    const after = rest.splitText(spot.word.length);
    const mark = el("span", { class: "tx-hit", text: spot.word });
    rest.replaceWith(mark);
    void after;
    follow(mark);                                             // листаем к СЛОВУ, а не к куску: кусок бывает выше окна
    busy = now + hold + (ok ? CUT_MS : 0) + gap;

    // Зачёркивание — ОТДЕЛЬНЫЙ шаг перед заменой, и только у принятой правки: мгновенная
    // подмена читается как опечатка показа — глаз не успевает увидеть, ЧТО именно исправили.
    const cut = () => mark.classList.add("cut");

    const land = () => {
      // ⚠️ В тексте остаётся ИСПРАВЛЕННОЕ слово (оранжевым), а прежнее уходит в слой над
      // текстом и всплывает по наведению (решение владельца 24.09). Конец работы — это готовая
      // расшифровка, а не лист корректуры: читать надо то, что получилось.
      // ⚠️ Отвергнутая правка текст НЕ меняет вовсе — остаётся только точечная пометка, иначе
      // наводить было бы незачем и некуда.
      // ⚠️ Слова «было» не нужно: зачёркнутое слово и значит «было и ушло» — подпись к
      // очевидному только занимает место. У отвергнутой правки зачёркнута ПРЕДЛОЖЕННАЯ замена
      // (её в тексте нет), а рядом — почему.
      const gone = ok ? spot.word : e.now;
      const note = ok ? "" : [WHY[e.why] || e.why || "",
                              e.term ? `«${e.term}»` : ""].filter(Boolean).join(" ");
      // ⚠️ Простые `span`, а НЕ `ruby`/`rt`: у `ruby` своя раскладка, а у `inline-block`, которым
      // её приходилось гасить, — своя высота и свои точки переноса: строка с правкой становилась
      // выше соседних, а знак препинания за словом уезжал на следующую. Замена обязана
      // вести себя как обычное слово — иначе текст дёргается на каждой правке.
      const fix = el("span", { class: ok ? "ed fresh" : "ed no fresh" },
        el("span", { class: ok ? "ed-now" : "ed-kept", text: ok ? e.now : spot.word }));
      const rt = el("span", { class: "ed-old" }, el("s", { class: "ed-gone", text: gone }));
      if (note) rt.append(el("span", { class: "ed-note", text: note }));
      fix.append(rt);
      mark.replaceWith(fix);
      marks.push(fix);
      fit(rt);
      setTimeout(() => fix.classList.remove("fresh"), FRESH_MS);
    };

    if (reduced()) land();
    else if (ok) { setTimeout(cut, hold); setTimeout(land, hold + CUT_MS); }
    else setTimeout(land, hold);
  }

  /** Слой со старым словом — внутрь окна. Он висит над словом, а слово бывает у самого края:
   * без сдвига фраза уезжала бы за границу (у окна `overflow-x:hidden`) и обрезалась молча. */
  function fit(node) {
    if (typeof node.getBoundingClientRect !== "function") return;
    node.style.marginLeft = "0px";
    const over = node.getBoundingClientRect().right - (body.getBoundingClientRect().right - 10);
    if (over > 0) node.style.marginLeft = `${-over}px`;
  }

  function summary() {
    count.textContent = applied || dropped
      ? `принято ${applied} · отброшено ${dropped}${turns ? ` · реплик ${turns}` : ""}` : "";
  }

  return {
    apply(e) {
      if (e.t === "stage.start" && e.stage === "pass2") {
        root.removeAttribute("hidden");
        mode = "writing";
        head.textContent = "распознаю — текст появляется по мере готовности";
        return;
      }
      if (e.t === "chunk.start") { current = e.from || current; return; }
      if (e.t === "chunk.done" && mode === "writing" && e.raw) { append(current, e.raw); return; }
      if (e.t === "stage.start" && e.stage === "final-round") {
        root.removeAttribute("hidden");
        mode = "fixing";
        head.textContent = "правлю сущности — исправленное оранжевым; наведите, чтобы увидеть прежнее";
        return;
      }
      if (e.t === "turn.text") {
        current = e.start || 0;
        // Текста ещё нет (окно открыли на середине прогона) — показываем хотя бы эту реплику.
        if (!body.children.length) append(current, e.text || "");
        else follow(near(current));
        return;
      }
      if (e.t === "turn.fix") { jobs.push(e); pump(); return; }
      if (e.t === "turn.done") { turns = e.n || turns; summary(); }
    },
    reset() {
      mode = "idle"; pending = ""; typed = 0; tail = null; jobs.length = 0; busy = 0;
      applied = dropped = turns = current = 0;
      marks = []; anchors = [];
      head.textContent = ""; body.replaceChildren(); count.textContent = "";
      root.setAttribute("hidden", "");
    },
    state: () => ({ mode, applied, dropped, turns, text: body.textContent,
                    edits: marks.length, queued: jobs.length }),
  };
}
