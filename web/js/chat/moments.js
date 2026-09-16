// Карточка-момент: заголовок доклада, тайм-код, волна и расшифровка чанка.
//
// Взаимодействия (каждое ведёт к своей цели):
//   ▶            — послушать момент прямо здесь
//   номер / ↗    — открыть запись НА ЭТОЙ секунде
//   клик по карточке — развернуть расшифровку фрагмента
import { el, fmt } from "../ui/dom.js";
import * as player from "../ui/player.js";
import { getWords } from "../api.js";
import { buildKaraoke, follower } from "../ui/karaoke.js";
import { hotSpans } from "./gist.js";

const PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const PAUSE =
  '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
const SHARE =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M12 15V3M8 7l4-4 4 4"/></svg>';
const EXT =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>';
const NEXT =
  '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 5l9 7-9 7zM17 5h2v14h-2z"/></svg>';
const BACK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 17l-5-5 5-5M18 17l-5-5 5-5"/></svg>';

const WAVE_BARS = 46;
const FALLBACK_SPAN = 30; // если сервер не смог вычислить конец чанка
// «Шире» ходит парами реплик: по одной — слишком мелкий шаг, вопрос и ответ на
// него почти всегда рядом. Потолок тот же, что у сервера.
const PAD_STEP = 2;
const MAX_PAD = 6;
// Клик по волне в пределах этого промежутка перед важным местом ведёт в его
// начало: волна в карточке шириной с ладонь, и точность там пальцем не берётся.
const WAVE_SNAP = 3;
// Сколько мест ответа показывать целиком: цитата иногда подпирает полтекста,
// и карточка превращалась бы в пересказ ответа.
const MAX_CLAIMS = 2;

function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Строки чанка приходят как «[37:07] [Спикер] текст», а продолжения — без времени.
const LINE_WITH_TIME = /^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?:\[([^\]]+)\]\s*)?(.*)$/;
const LINE_WITH_SPEAKER = /^\[([^\]]+)\]\s*(.*)$/;

function toSeconds(tc) {
  const parts = tc.split(":").map(Number);
  return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
}

/** Разбирает расшифровку чанка: время и спикер — отдельными полями, а не куском текста. */
function buildTranscript(container, citation) {
  const segments = [];
  let currentSec = citation.sec ?? 0;

  for (const raw of (citation.text || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;

    let tc = "";
    let speaker = "";
    let text = line;

    const timed = LINE_WITH_TIME.exec(line);
    if (timed) {
      [, tc, speaker = "", text] = timed;
      currentSec = toSeconds(tc);
    } else {
      const spoken = LINE_WITH_SPEAKER.exec(line);
      if (spoken) [, speaker, text] = spoken;
    }

    const seg = el(
      "div",
      { class: "tx-seg", title: tc ? `Слушать с ${tc}` : "" },
      tc ? el("span", { class: "tx-tc", text: tc }) : null,
      speaker ? el("span", { class: "tx-who", text: speaker }) : null,
      el("span", { class: "tx-txt", text })
    );
    const at = currentSec;
    seg.addEventListener("click", (event) => {
      event.stopPropagation();
      if (citation.url) player.play(citation.url, { at, owner: `m${citation.n}`, record: citation.rec });
    });
    container.append(seg);
    segments.push({ sec: at, node: seg });
  }
  return segments;
}

// Инструменты движка — человеческим языком. Generic: это не про наш корпус, а про
// то, как агент вообще ищет.
const TOOLS = {
  search: "поиск по базе",
  get_doc: "чтение документа целиком",
  find_section: "поиск раздела",
  lookup: "справочник",
  catalog: "каталог",
};

/**
 * «Почему этот фрагмент здесь» — из провенанса движка.
 *
 * Это единственный честный ответ на вопрос: подсветка ключевых слов говорит
 * лишь о совпадении корней, а тут — настоящий запрос агента и то, каким шагом
 * фрагмент найден. Найденный несколько раз разными формулировками — сильный
 * сигнал: агент возвращался к этому месту, а не наткнулся случайно.
 */
function buildProvenance(found) {
  if (!found?.length) return null;
  // Свёрнуто по умолчанию: это протокол работы движка, а не то, ради чего
  // цитату открыли. Нужен он редко — но когда нужен, других источников нет.
  const box = el("details", { class: "m-prov" });
  const first = found[0];
  const how0 = TOOLS[first.tool] || first.tool || "поиск";
  box.append(
    el("summary", { class: "m-prov-sum" }, el("span", { text: `как нашли · ${how0}` }))
  );
  if (first.query) {
    box.append(
      el(
        "div",
        { class: "m-prov-row" },
        el("span", { class: "m-prov-key", text: "искали" }),
        el("span", { class: "m-prov-q", text: `«${first.query}»` })
      )
    );
  }
  const how = [TOOLS[first.tool] || first.tool, first.rank ? `${first.rank}-й в выдаче` : ""]
    .filter(Boolean)
    .join(" · ");
  const again = found.length > 1 ? ` · находилось ${found.length} раза` : "";
  if (how || again) {
    box.append(
      el(
        "div",
        { class: "m-prov-row" },
        el("span", { class: "m-prov-key", text: "как нашли" }),
        el("span", { text: `${how}${again}` })
      )
    );
  }
  return box;
}

function buildWave(seed) {
  const wave = el("div", { class: "wave" });
  const rnd = seeded(seed);
  for (let i = 0; i < WAVE_BARS; i++) {
    const envelope = Math.sin((i / WAVE_BARS) * Math.PI);
    const height = 16 + Math.round((0.25 + 0.75 * rnd()) * envelope * 84);
    wave.append(el("span", { class: "bar", style: `height:${Math.min(100, height)}%` }));
  }
  wave.append(el("span", { class: "playhead" }));
  return wave;
}

/** @param {object} c кадр citation из потока */
export function momentCard(c, { onOpenRecord, onShareMoment, onOpen } = {}) {
  const label = c.label || "";
  // метка приходит как «Заголовок · 37:07 · Спикеры» — заголовок сам содержит « · »,
  // поэтому разбираем с конца и только для показа (запись и секунда уже пришли полями)
  const parts = label.split(" · ");
  const speakers = parts.length > 2 ? parts.pop() : "";
  const tc = parts.length > 1 ? parts.pop() : fmt(c.sec || 0);
  const title = parts.join(" · ");

  const start = c.sec || 0;
  const span = Math.max(5, (c.end ?? start + FALLBACK_SPAN) - start);

  const card = el("article", { class: "moment", id: `m-${c.n}`, "data-n": c.n });
  const playBtn = el("button", { class: "play", html: PLAY, "aria-label": `Слушать с ${tc}` });
  // Заголовок доклада — он же ссылка в читалку. У подкаста рядом с ним стоял бейдж «№23»,
  // но идентификатор записи — это дата и слаг («2026-03-12-kafka»), и в бейдже он читался бы
  // хуже самого заголовка. Поэтому кликабельным делаем заголовок, а бейдж убираем.
  const recTag = el("span", {
    class: "rec",
    text: title,
    title: c.rec ? "Открыть запись на этом моменте" : "",
  });

  const tcTag = el("span", { class: "tc", text: tc });
  // Подпись момента: что за доклад, кто говорит и на какой минуте. Стоит внизу,
  // прямо над плеером: пока читаешь расшифровку, эти сведения не нужны, а вот
  // когда собираешься слушать или сослаться — нужны все три сразу.
  const head = el(
    "div",
    { class: "m-head" },
    el("span", { class: "m-num", text: String(c.n) }),
    el("div", { class: "m-title" }, c.rec ? recTag : title),
    el("div", { class: "m-speakers", text: speakers }),
    tcTag
  );

  const openBtn = el("a", {
    class: "m-open",
    href: "#",
    html: EXT,
    title: "Открыть запись на этом моменте",
    "aria-label": "Открыть запись на этом моменте",
  });
  // Ссылка на момент: получатель откроет читалку ровно на этой секунде. Кнопка
  // живёт рядом с «открыть», потому что это одно и то же действие — с той
  // разницей, что одно для себя, а другое для другого человека.
  const shareBtn = el("button", {
    class: "m-share",
    html: SHARE,
    title: "Скопировать ссылку на этот момент",
    "aria-label": "Скопировать ссылку на этот момент",
  });
  shareBtn.addEventListener("click", (event) => {
    event.preventDefault();
    onShareMoment?.(c.rec, Math.floor(start));
  });

  // Дорога к метке в тексте ответа. Цитату открывают, чтобы проверить
  // утверждение, — и, проверив, хотят вернуться ровно туда, а не искать глазами,
  // где читали. Как в электронных читалках.
  //
  // Кнопка есть у ЛЮБОЙ карточки, чья сноска стоит в ответе, а не только у той,
  // куда пришли кликом: из нижнего списка дорога в текст нужна ровно так же.
  const backLabel = el("span", { text: `к [${c.n}] в ответе` });
  const backBtn = el(
    "button",
    { class: "m-back", hidden: "", title: "Вернуться к этому месту ответа" },
    el("span", { class: "m-back-ic", html: BACK }),
    backLabel
  );
  let backTo = null;
  /** Вернуть взгляд к сноске в тексте ответа и подсветить её саму. */
  function goToRef(node) {
    if (!node?.isConnected) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    // Подсвечиваем саму сноску: на середине экрана оказывается абзац, а вернуть
    // надо взгляд к конкретной цифре в нём.
    node.classList.remove("ref-flash");
    void node.offsetWidth; // перезапуск анимации, иначе второй возврат её не покажет
    node.classList.add("ref-flash");
  }
  backBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    goToRef(backTo);
  });

  // Чем цитата подпирает ответ. Самое честное объяснение «зачем она здесь»:
  // не ранжирование и не совпадение слов, а место, ради подкрепления которого
  // агент её и привёл. Даром: считается из уже полученного текста ответа.
  const claimBox = el("div", { class: "m-claim", hidden: "" });

  const wave = buildWave((c.sec || 1) * 7 + c.n);
  const bars = [...wave.querySelectorAll(".bar")];
  const playhead = wave.querySelector(".playhead");
  const foot = el("div", { class: "m-foot" }, wave);
  const transcript = el("div", { class: "tx", hidden: "" });
  const caption = () => el("div", { class: "tx-cap", text: "Расшифровка фрагмента" });
  transcript.append(caption());
  let segments = buildTranscript(transcript, c);
  let karaoke = null;
  let track = null; // слежение прокрутки, уступающее человеку

  // Что именно сейчас играем. Обычно — весь чанк, и тогда карточка сама
  // останавливается на его конце: человек просил момент, а не всё, что после.
  // А «важное» проигрывает выделенные куски подряд, перепрыгивая воду между ними.
  let queue = null;
  let step = 0;
  let hot = []; // выделенные места цитаты (`gist.js`)
  let spoken = []; // загруженные реплики со словами — по ним пересобираем выделения
  let claims = []; // утверждения ответа с этой цитатой (приходят, когда ответ дописан)
  let pad = 0; // сколько реплик разговора подтянуто вокруг («шире»)
  // Докуда играем и куда возвращаемся, доиграв. Не константы, потому что цитата
  // — не единственное, что показано: подтянув контекст, человек может ткнуть в
  // слово ЗА её концом, и глушить его там нельзя (см. `listenFrom`).
  const chunkEnd = c.end ?? start + FALLBACK_SPAN;
  let limit = chunkEnd;
  let playFrom = start;
  let viewEnd = chunkEnd; // конец показанного текста, вместе с контекстом

  // Кнопки стоят В НАЧАЛЕ расшифровки, а не под ней: «только важное» — это
  // предложение не читать полторы минуты целиком, и предлагать его надо до
  // текста, а не после того, как человек его уже промотал.
  const tools = el("div", { class: "m-tools" });
  const coreBtn = el("button", { class: "m-tool m-tool-key", hidden: "" });
  const nextBtn = el("button", {
    class: "m-tool",
    html: `${NEXT}<span>следующее</span>`,
    hidden: "",
    title: "Перейти к следующему важному месту",
  });
  const widerBtn = el("button", { class: "m-tool", text: "шире", hidden: "", title: "Показать разговор вокруг" });
  const narrowBtn = el("button", { class: "m-tool", text: "уже", hidden: "", title: "Убрать контекст" });
  // Все действия — одной строкой наверху карточки: и свои («только важное»,
  // «следующее», «шире»), и внешние («ссылка», «в читалку»). Раньше вторые
  // жили в шапке рядом с номером, и получалось два ряда кнопок в разных углах.
  tools.append(coreBtn, nextBtn, widerBtn, narrowBtn, el("span", { class: "m-tools-gap" }));
  if (c.rec) tools.append(shareBtn);
  tools.append(openBtn);

  /**
   * Подменяет расшифровку на пословную, когда у нас есть тайм-коды слов.
   *
   * Тогда карточка показывает НАШ текст, а не пришедший от движка: в индексе
   * живут чанки старых версий расшифровок, и после ручных правок они с
   * корпусом расходятся. Свой текст ещё и подсвечивается по словам.
   *
   * Фрагмент показываем целиком, но с выделенными местами (`gist.js`): чем он
   * релевантен, движок уже сказал сам — словами, которые отметил в чанке.
   * Взгляд наводим на первое выделение, иначе полторы минуты устной речи
   * начинаются с середины чужой мысли.
   *
   * Грузим лениво — при первом раскрытии: цитат в ответе бывает под два
   * десятка, и тянуть слова для всех сразу незачем.
   */
  let wordsAsked = false;
  let loading = null; // тот же запрос, если его уже ждут

  /** Слова нужны и раскрытию карточки, и «играть» — ждут они одну загрузку. */
  function ensureWords(nextPad = pad) {
    if (!loading || nextPad !== pad) {
      loading = upgradeTranscript(nextPad).finally(() => {
        loading = null;
      });
    }
    return loading;
  }

  async function upgradeTranscript(nextPad = pad) {
    if (!c.rec || c.sec == null) return;
    if (wordsAsked && nextPad === pad && karaoke) return;
    wordsAsked = true;
    try {
      const data = await getWords(c.rec, { start: c.sec, end: chunkEnd, pad: nextPad });
      if (!data.aligned || !data.turns.length) return;
      pad = nextPad;
      spoken = data.turns;
      render();
    } catch {
      // не вышло — остаётся построчная расшифровка от движка, она рабочая
    }
  }

  /**
   * Собрать расшифровку заново по уже загруженным словам.
   *
   * Зовётся дважды: когда слова приехали и когда стало известно утверждение
   * ответа — второе меняет ВЫДЕЛЕНИЯ, а не текст, но перерисовать проще, чем
   * переставлять обёртки в готовом дереве.
   */
  function render() {
    if (!spoken.length) return;
    // Важные места ищем ТОЛЬКО внутри цитаты, даже когда вокруг подтянут
    // контекст. Иначе ключевые слова из соседних реплик рождают места за
    // границей чанка: на волне их нет (она в масштабе цитаты), а перемотку
    // туда карточка сама же и гасит, останавливая звук на конце чанка, —
    // получается «мест три, а работают два» (ловили на живых цитатах).
    const inCite = pad
      ? spoken
          .map((t) => ({ ...t, words: t.words.filter((w) => w[1] >= start && w[2] <= chunkEnd) }))
          .filter((t) => t.words.length)
      : spoken;
    hot = hotSpans(inCite, c.keywords || [], claims).spans;
    // Докуда показан текст: до этой секунды разрешено играть, если человек
    // сам ткнул в слово за концом цитаты.
    viewEnd = Math.max(chunkEnd, spoken.at(-1)?.end ?? chunkEnd);
    const built = buildKaraoke(spoken, {
      onSeek: listenFrom,
      keywords: c.keywords || [],
      spans: hot,
      inside: pad ? { start: c.sec, end: chunkEnd } : null,
    });
    built.node.classList.add("kar-inline");
    karaoke?.dispose?.();
    // Служебное — под текст, одной строкой: подпись «расшифровка фрагмента» и
    // свёрнутый протокол поиска. Над текстом им не место, там читают.
    const foot = el("div", { class: "tx-foot" }, caption(), buildProvenance(c.found_by));
    transcript.replaceChildren(claimBox, built.node, foot);
    karaoke = built;
    refreshTools();
    focusFirstHot();
    // Подсветка теперь меняется на КАЖДОМ слове, а не раз в реплику: без
    // выдержки прокрутка дёргалась бы постоянно и листать руками стало бы
    // невозможно. follower отступает, пока человек крутит сам.
    track?.stop?.();
    track = follower(transcript, { pauseMs: 4000 });
    segments = []; // построчная подсветка больше не нужна
  }

  /**
   * Слушать с этой секунды.
   *
   * Здесь же решается, докуда играть. Обычно — до конца цитаты: человек просил
   * момент, а не всё, что после него. Но если ткнули в слово ЗА её концом (так
   * бывает только с подтянутым контекстом), глушить на конце цитаты нельзя —
   * звук обрывался бы через мгновение после старта и прыгал в начало чанка.
   * Тогда играем до конца показанного текста.
   */
  function listenFrom(at) {
    if (!c.url) return;
    queue = null; // слушаем подряд, а не по важным местам
    playFrom = at;
    limit = at > chunkEnd ? viewEnd : chunkEnd;
    player.play(c.url, { at, owner: `m${c.n}`, title: title || `Момент ${tc}`, record: c.rec });
    showWordAt(at);
  }

  /**
   * Подвести текст к тому месту, откуда пошёл звук.
   *
   * Перемотка волной уводила голос куда-то в середину фрагмента, а расшифровка
   * оставалась там, где была: звук идёт, а нужные слова приходится искать
   * руками. Слежение само подтянет текст только на следующем слове и только
   * если человек до этого не крутил вручную — поэтому наводимся явно.
   *
   * Но лишь когда слово и правда не видно: клик по слову, которое перед
   * глазами, дёргать страницу не должен.
   */
  function showWordAt(at) {
    if (!karaoke || transcript.hasAttribute("hidden")) return;
    const word = karaoke.wordAt(at);
    if (word && track?.lost(word)) track.jump(word);
  }

  /**
   * Показать первое выделенное место, а не начало чанка.
   *
   * Цитата вырезана из разговора и начинается с середины чужой мысли, поэтому
   * первые строки почти всегда «ну, как бы, смотрите». Чуть выше выделения
   * оставляем пару строк разгона — иначе оно упирается в край и читается как
   * начало текста, хотя перед ним ещё есть речь.
   */
  function focusFirstHot() {
    const box = karaoke?.firstHot;
    if (!box?.offsetTop) return;
    transcript.scrollTop = Math.max(0, box.offsetTop - transcript.clientHeight / 3);
  }

  /** Кнопки говорят о текущем состоянии текста, а не о наших намерениях. */
  function refreshTools() {
    if (!karaoke) return;
    const hotSec = hot.reduce((sum, s) => sum + (s.end - s.start), 0);
    coreBtn.toggleAttribute("hidden", !hot.length);
    if (hot.length) {
      // Сколько мест — важная часть ответа: «одно место на 12 секунд» и «три
      // места по всему фрагменту» читаются по-разному ещё до нажатия.
      const where = hot.length > 1 ? `${hot.length} места · ` : "";
      coreBtn.innerHTML = `${PLAY}<span>только важное · ${where}${fmt(hotSec)}</span>`;
    }
    nextBtn.toggleAttribute("hidden", hot.length < 2);
    widerBtn.toggleAttribute("hidden", pad >= MAX_PAD);
    narrowBtn.toggleAttribute("hidden", pad === 0);
    paintWave();
  }

  /**
   * Важные места — прямо на волне.
   *
   * Волна и была картой фрагмента, но одинаковой по всей длине: где именно
   * говорят по делу, из неё не следовало. Теперь по ней можно перематывать, не
   * читая текст, — и понятно, куда.
   */
  function paintWave() {
    for (let i = 0; i < bars.length; i++) {
      const at = start + (span * (i + 0.5)) / bars.length;
      bars[i].classList.toggle("hot", hot.some((s) => at >= s.start && at <= s.end));
    }
  }

  /** Секунда под курсором на волне; клик рядом с важным местом ведёт в его начало. */
  function waveTime(event) {
    const box = wave.getBoundingClientRect();
    const at = start + span * Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    // Промах в пару секунд не должен ронять человека в середину фразы: рядом с
    // выделенным местом отправляем в его начало, где мысль начинается.
    const near = hot.find((s) => at >= s.start - WAVE_SNAP && at <= s.end);
    return near ? near.start : at;
  }

  wave.addEventListener("click", (event) => {
    event.stopPropagation();
    if (!c.url) return;
    listenFrom(waveTime(event));
  });

  /** Следующее важное место после текущей секунды (по кругу). */
  nextBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (hot.length < 2 || !c.url) return;
    const now = player.state().time ?? start;
    listenFrom((hot.find((s) => s.start > now + 0.5) || hot[0]).start);
  });

  coreBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (!hot.length || !c.url) return;
    queue = hot;
    step = 0;
    player.play(c.url, { at: hot[0].start, owner: `m${c.n}`, title: title || `Момент ${tc}`, record: c.rec });
    showWordAt(hot[0].start);
  });
  widerBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    ensureWords(Math.min(MAX_PAD, pad + PAD_STEP));
  });
  narrowBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    ensureWords(Math.max(0, pad - PAD_STEP));
  });

  card.append(backBtn, tools, transcript, head, playBtn, foot);

  const openRecord = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (c.rec && onOpenRecord) onOpenRecord(c.rec, c.sec ?? 0);
  };
  recTag.addEventListener("click", openRecord);
  openBtn.addEventListener("click", openRecord);

  playBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (!c.url) return;
    // Пауза — сразу: ждать загрузки слов, чтобы остановить звук, незачем.
    const s = player.state();
    if (s.playing && s.url === c.url && s.owner === `m${c.n}`) {
      player.pause();
      return;
    }
    setTranscript(true);
    startPlay();
  });

  card.addEventListener("click", (event) => {
    if (event.target.closest(".tx") || event.target.closest(".m-open")) return;
    setTranscript(transcript.hasAttribute("hidden"));
  });

  /**
   * Показать утверждение ответа, которое подпирает эта цитата.
   *
   * Клик по нему ведёт туда же, куда «← назад»: прочитал, ради чего цитата, —
   * и сразу можешь вернуться к этому месту в ответе.
   */
  function setClaim(list, refNode) {
    offerBack(refNode); // сноска этой цитаты в тексте — теперь к ней можно прыгнуть
    const changed = list.join("|") !== claims.join("|");
    claims = list;
    // Утверждение — лучший источник выделений, чем поисковые слова: в нём
    // конкретика, ради которой цитату и привели. Пришло позже текста — значит
    // пересобираем подсветку, если расшифровка уже построена.
    if (changed && karaoke) render();
    claimBox.replaceChildren();
    claimBox.toggleAttribute("hidden", !claims.length);
    if (!claims.length) return;
    claimBox.append(el("div", { class: "m-prov-key", text: "подпирает" }));
    for (const text of claims.slice(0, MAX_CLAIMS)) {
      claimBox.append(el("blockquote", { class: "m-claim-q", text }));
    }
    if (claims.length > MAX_CLAIMS) {
      claimBox.append(
        el("div", { class: "m-claim-more", text: `и ещё в ${claims.length - MAX_CLAIMS} месте ответа` })
      );
    }
    if (refNode) {
      claimBox.classList.add("clickable");
      claimBox.title = "Показать это место в ответе";
      claimBox.onclick = (event) => {
        event.stopPropagation();
        goToRef(refNode);
      };
    }
  }

  /** Запомнить метку в тексте и показать дорогу к ней. */
  function offerBack(node) {
    if (node) backTo = node;
    backBtn.toggleAttribute("hidden", !backTo);
  }

  function setTranscript(open, { silent = false } = {}) {
    transcript.toggleAttribute("hidden", !open);
    card.classList.toggle("open", open);
    if (open) {
      ensureWords();
      // Раскрытие идёт тремя путями (клик по карточке, инлайн-ссылка, тайм-код
      // в сводке) — и в каждом список обязан свернуть остальные. Сообщаем о
      // раскрытии наверх, чтобы это правило жило в одном месте, а не в трёх.
      if (!silent) onOpen?.(c.n);
    }
  }

  // Состояние звука приходит каждый кадр — значит писать в DOM можно только то,
  // что реально изменилось. `innerHTML` заново разбирает SVG, а столбиков волны
  // почти полсотни: на 60 кадрах это уже ощутимая работа впустую.
  let wasPlaying = null;
  let litBars = -1;
  let onAir = -1; // какое важное место звучит сейчас (чтобы не писать в DOM зря)
  const unsubscribe = player.subscribe((s) => {
    const mine = s.url === c.url && s.owner === `m${c.n}`;
    playBtn.classList.toggle("playing", mine && s.playing);
    const playing = mine && s.playing;
    if (playing !== wasPlaying) {
      playBtn.innerHTML = playing ? PAUSE : PLAY;
      wasPlaying = playing;
    }
    playhead.classList.toggle("on", mine);
    if (!mine) {
      bars.forEach((b) => b.classList.remove("on"));
      litBars = -1;
      segments.forEach((s) => s.node.classList.remove("on"));
      karaoke?.clear(); // играет другой момент — гасим и пословную подсветку
      karaoke?.setActiveSpan(-1);
      queue = null;
      tcTag.textContent = tc;
      return;
    }
    // Момент кончился — останавливаемся сами. Иначе запись едет дальше по
    // записи: человек просил послушать цитату, а не всё, что после неё.
    if (queue) {
      // Слушаем выжимку: кусок отзвучал — прыгаем к следующему, а после
      // последнего останавливаемся и возвращаемся к её началу.
      if (s.playing && s.time >= queue[step].end + 0.35) {
        const next = queue[step + 1];
        if (next) {
          step += 1;
          player.seek(next.start);
          showWordAt(next.start);
        } else {
          const home = queue[0].start;
          queue = null;
          player.pause();
          player.seek(home);
        }
        return;
      }
    } else if (s.playing && s.time >= limit + 0.4) {
      // Докуда играть, решено при запуске (`listenFrom`), и возвращаемся туда
      // же, откуда начали: прыжок в начало чанка после клика по слову в конце
      // выглядел как самоуправство плеера.
      player.pause();
      player.seek(playFrom);
      return;
    }

    highlight(s.time);
    // Важное место отмечается всегда, когда звук в него попал, — а не только
    // при игре «только важного»: перемотали руками на волне, и сразу видно,
    // в какое из мест попали.
    const air = hot.findIndex((x) => s.time >= x.start && s.time <= x.end + 0.3);
    if (air !== onAir) {
      karaoke?.setActiveSpan(air);
      onAir = air;
    }
    // масштаб волны — по реальной длине чанка (её посчитал сервер по расшифровке)
    const offset = Math.max(0, s.time - (c.sec || 0));
    const progress = Math.max(0, Math.min(1, offset / span));
    playhead.style.left = `${progress * 100}%`;
    const lit = Math.floor(progress * bars.length);
    if (lit !== litBars) {
      bars.forEach((b, i) => b.classList.toggle("on", i <= lit));
      litBars = lit;
    }
    tcTag.textContent = fmt(s.time);
  });
  // ⚠️ Раньше здесь стояло `card.addEventListener("remove", unsubscribe)` — а события «remove»
  // у DOM нет, и подписка не снималась НИКОГДА: каждая карточка каждого хода слушала плеер до
  // перезагрузки страницы. Снимает её `dispose()` ниже; зовёт блок моментов при уходе хода.

  // Подсветка: по словам, если тайм-коды слов загрузились, иначе по репликам.
  let lit = null;
  function highlight(time) {
    if (karaoke) {
      const word = karaoke.at(time);
      if (word && !transcript.hasAttribute("hidden")) track?.follow(word);
      return;
    }
    let active = null;
    for (const seg of segments) {
      if (seg.sec <= time + 0.25) active = seg;
      else break;
    }
    if (active === lit) return;
    lit?.node.classList.remove("on");
    active?.node.classList.add("on");
    lit = active;
    if (active && !transcript.hasAttribute("hidden")) {
      // держим активную строку в поле зрения, не трогая скролл всей страницы
      const top = active.node.offsetTop - transcript.clientHeight / 2 + active.node.offsetHeight / 2;
      transcript.scrollTop = Math.max(0, top);
    }
  }

  /**
   * Начать слушать момент — с его начала и до конца чанка.
   *
   * ⚠️ Стартовать с первого важного места ПРОБОВАЛИ и откатили (2026-08-16):
   * для этого нужны тайм-коды слов, а `await` за ними уводит `play()` из
   * обработчика жеста — браузер такое воспроизведение блокирует, и кнопка
   * просто переставала работать. Та же грабля, что с переходом по ссылке на
   * момент. К важному месту ведут волна, «⏭ следующее» и «только важное» —
   * все три запускают звук прямо в обработчике клика.
   */
  function startPlay() {
    listenFrom(start);
  }

  return {
    card,
    /** Снять подписку на плеер и слежение за прокруткой: карточка уходит вместе с ходом. */
    dispose() {
      unsubscribe();
      track?.stop?.();
      karaoke?.dispose?.();
    },
    // `play` нужен, когда момент открывают тайм-кодом из свёрнутой сводки:
    // человек ткнул во время — значит хочет его услышать, а не просто увидеть.
    expand: ({ play = false, from = null } = {}) => {
      setTranscript(true, { silent: true }); // раскрывает сам список — эхо ему не нужно
      offerBack(from);
      if (play) startPlay();
    },
    collapse: () => setTranscript(false),
    setClaim,
    /**
     * Подвести взгляд к первому важному месту — без звука.
     *
     * Нужно, когда карточка переезжает во врезку: расшифровка там открывается
     * сразу, и упереться взглядом в начало фрагмента («ну, как бы, смотрите»)
     * значит потерять то, ради чего цитату и раскрыли. Ждём слова: без них
     * выделений ещё нет.
     */
    async focus() {
      await ensureWords();
      focusFirstHot();
    },
  };
}
