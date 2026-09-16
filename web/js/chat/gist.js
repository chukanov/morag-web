// Суть цитаты: какие куски чанка показывать, а какие прятать.
//
// Чанк бывает на полторы минуты живой устной речи — с «ну», «как бы» и мыслью
// вслух. Читать это целиком, чтобы понять, при чём тут ответ, невозможно.
// Поэтому карточка по умолчанию показывает только горячие куски.
//
// Что считать горячим, придумываем не мы: движок сам отметил `<mark>` слова,
// чьи корни встречались в поисковых запросах агента, — то есть сказал, ЧЕМ
// фрагмент оказался релевантен. BFF отдаёт эти словоформы в кадре цитаты.
//
// Модуль чистый (только числа и строки) — потому и проверяется в node, без
// браузера: ошибка здесь незаметна глазом, а прячет она настоящую речь.

// Фразу режем по концу предложения: обрывок «…позволяет обучать модель» без
// начала читается хуже, чем лишние пять слов контекста.
const SENTENCE_END = /[.!?…]["»)]?$/;
const CLEAN = /[^\p{L}\p{N}-]/gu;

// Длинная фраза (устная речь редко ставит точки) — не повод показывать полминуты:
// вокруг ключевого слова хватает и десятка слов в каждую сторону.
const MAX_PHRASE_WORDS = 26;
const AROUND = 9;

// Сколько чанка позволено оставить видимым. Больше — сжатие перестаёт быть
// сжатием, и «показать целиком» теряет смысл.
const MAX_SHARE = 0.5;
const MAX_SPANS = 3;

// Куски ближе этого промежутка сливаем: два многоточия подряд с одним словом
// между ними выглядят как помеха, а не как выжимка.
const GLUE_SEC = 3.5;

// Предел склейки. Без него куски срастаются в один сплошной: замерено на живой
// цитате — ключевые слова размазаны по всему чанку (16 попаданий на 308 слов),
// и слияние «соседних» съедало фрагмент целиком, показывая 100% под видом сути.
const MAX_GROUP_WORDS = 48;

// Если видимого всё равно почти столько же — прятать нечего. Честнее показать
// фрагмент как есть, чем рисовать многоточия и кнопку «показать целиком» ради
// пары спрятанных слов.
const POINTLESS_SHARE = 0.66;

// Дословное совпадение весит больше разрозненных слов: два слова подряд из
// утверждения — это уже цитата, а не случайность. Замерено на живом ответе:
// без этого пассаж «выступает в роли роутера» проигрывал месту, где просто
// набралось побольше общих слов вроде «модель» и «задача».
const PAIR_BOOST = 4;
const MIN_TERM_WEIGHT = 0.25; // чтобы бонус получило и слово, не прошедшее в термы

// Слова, которые есть в любой фразе и потому ничего не выделяют. Короткий
// список: длинные слова отсеиваются порогом длины, а тонкая чистка — это уже
// стемминг, ради которого браузеру пришлось бы тащить словарь.
const STOP = new Set(
  ("если когда потому чтобы этот эта это эти тот та те там тут который которая которые " +
    "может можно нужно надо было были быть есть очень ещё уже даже только тоже также " +
    "будет будут более менее самый самая свои свой него неё них его её их мы вы они " +
    "как что чем чего кого кому про для над под без при через между всё все всех " +
    "говорил говорит сказал упоминал считал расценивал приводил например именно " +
    "который которую котором каком какой какая такие такой такое время сейчас")
    .split(/\s+/)
);

/** Слово без пунктуации и регистра — в таком виде его сравнивают с ключевыми. */
export function normalize(word) {
  return String(word || "").toLowerCase().replace(CLEAN, "");
}

/**
 * Совпадает ли слово с одной из ключевых форм.
 *
 * Сравниваем по общему началу, а не точным совпадением: движок стеммит
 * (Snowball), и в подсветку попадает та форма, что стояла в чанке индекса, —
 * «видеокарт», тогда как в нашем вычитанном тексте стоит «видеокарты». Тащить
 * стеммер в браузер ради этого не стоит, общего корня достаточно.
 */
export function isKey(word, keys) {
  const w = normalize(word);
  if (w.length < 3) return false;
  for (const key of keys) {
    const k = normalize(key);
    if (k.length < 3) continue;
    let i = 0;
    while (i < w.length && i < k.length && w[i] === k[i]) i++;
    if (i >= Math.max(3, Math.min(w.length, k.length) - 2)) return true;
  }
  return false;
}

/** Все слова подряд, с временами и весом совпадения с термами. */
function flatten(turns, terms, pairs = new Set()) {
  const flat = [];
  for (const turn of turns || []) {
    for (const [text, start, end] of turn.words || []) {
      let weight = 0;
      let hit = "";
      for (const [term, w] of terms) {
        if (w > weight && isKey(text, [term])) {
          weight = w;
          hit = term;
        }
      }
      flat.push({ text, start, end, key: weight > 0, weight, term: hit });
    }
  }
  // Идущие подряд слова утверждения — это уже не совпадение, а дословная цитата.
  // Агент часто пересказывает фрагмент почти буквально («выступает в роли
  // роутера»), и без этого такой участок проигрывал месту, где просто набралось
  // побольше разрозненных общих слов.
  for (let i = 0; i < flat.length - 1; i++) {
    const pair = `${normalize(flat[i].text)} ${normalize(flat[i + 1].text)}`;
    if (!pairs.has(pair)) continue;
    flat[i].weight = Math.max(flat[i].weight, MIN_TERM_WEIGHT) * PAIR_BOOST;
    flat[i + 1].weight = Math.max(flat[i + 1].weight, MIN_TERM_WEIGHT) * PAIR_BOOST;
    flat[i].key = true;
    flat[i + 1].key = true;
    flat[i].verbatim = true;
    flat[i + 1].verbatim = true;
    // Термы у слов РАЗНЫЕ (каждое своё), иначе дословная пара считается одним
    // совпадением и её отбрасывает правило «место должно покрывать разные слова».
    if (!flat[i].term) flat[i].term = normalize(flat[i].text);
    if (!flat[i + 1].term) flat[i + 1].term = normalize(flat[i + 1].text);
  }
  return flat;
}

/** Пары подряд идущих слов утверждения — по ним ловим дословные совпадения. */
function claimPairs(claims) {
  const pairs = new Set();
  for (const claim of claims) {
    const words = (String(claim).toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}-]*/gu) || []).map(normalize);
    for (let i = 0; i < words.length - 1; i++) {
      // пары из двух пустяковых слов («это очень») ничего не значат
      if (STOP.has(words[i]) && STOP.has(words[i + 1])) continue;
      if (words[i].length < 3 || words[i + 1].length < 3) continue;
      pairs.add(`${words[i]} ${words[i + 1]}`);
    }
  }
  return pairs;
}

/**
 * Термы утверждения с весами: чем реже слово во фрагменте, тем оно показательнее.
 *
 * Слово, встречающееся в чанке двадцать раз, не выделяет ничего — а «5000%» или
 * «кибербезопасность» указывают на место точно. Поэтому вес обратный частоте.
 */
function claimTerms(claims, words) {
  const terms = new Map();
  for (const claim of claims) {
    for (const raw of String(claim).toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}-]*/gu) || []) {
      const term = normalize(raw);
      if (term.length < 4 || STOP.has(term)) continue;
      if (terms.has(term)) continue;
      const hits = words.filter((w) => isKey(w, [term])).length;
      if (hits) terms.set(term, 1 / Math.sqrt(hits));
    }
  }
  return terms;
}

/** Границы фразы вокруг слова: до ближайшего конца предложения в обе стороны. */
function phraseAround(flat, i) {
  let from = i;
  while (from > 0 && !SENTENCE_END.test(flat[from - 1].text) && i - from < MAX_PHRASE_WORDS) from--;
  let to = i;
  while (to < flat.length - 1 && !SENTENCE_END.test(flat[to].text) && to - i < MAX_PHRASE_WORDS) to++;
  // Фраза оказалась монологом без единой точки — оставляем окно вокруг слова.
  if (to - from > MAX_PHRASE_WORDS) {
    from = Math.max(from, i - AROUND);
    to = Math.min(to, i + AROUND);
  }
  return [from, to];
}

/**
 * Важные места фрагмента.
 *
 * Источников два, и они разного качества.
 *
 * **Утверждение ответа** (`claims`) — то, ради подкрепления чего цитату привели.
 * Лучший источник: в нём конкретика («кибербезопасность», «5000%»), которая
 * указывает на место точно.
 *
 * **Слова поисковых запросов** (`keys`, они же `<mark>` движка) — запасной.
 * Они описывают ТЕМУ разговора, а не конкретную мысль, поэтому размазаны по
 * всему чанку: замерено — 16 попаданий на 308 слов, и подсвечивалось «клёвое
 * видео рекомендую к просмотру» вместо пассажа, ради которого цитата приведена.
 * Годятся, только пока утверждения нет (агент сослался не на каждую цитату).
 *
 * @param {object[]} turns   реплики со словами (как отдаёт `/words`)
 * @param {string[]} keys    ключевые словоформы из кадра цитаты
 * @param {string[]} claims  утверждения ответа с этой цитатой
 * @returns {{spans: {start,end,keys}[], share: number, source: string}}
 */
export function hotSpans(turns, keys, claims = []) {
  const words = (turns || []).flatMap((t) => (t.words || []).map((w) => w[0]));
  const fromClaim = claims.length ? claimTerms(claims, words) : new Map();
  const terms = fromClaim.size
    ? fromClaim
    : new Map((keys || []).map((k) => [k, 1]));
  const source = fromClaim.size ? "claim" : "keywords";

  const flat = flatten(turns, terms, claims.length ? claimPairs(claims) : new Set());
  if (!flat.length || !terms.size) return { spans: [], share: 1, source: "none" };

  // 1. Каждое ключевое слово тянет за собой свою фразу.
  const groups = [];
  for (let i = 0; i < flat.length; i++) {
    if (!flat[i].key) continue;
    const [from, to] = phraseAround(flat, i);
    const last = groups[groups.length - 1];
    // 2. Слипшиеся фразы объединяем сразу: соседние ключевые слова почти всегда
    //    сидят в одном месте разговора, и дробить его незачем. Но до предела —
    //    иначе цепочка склеек проглатывает весь чанк.
    const merged = last && Math.max(last.to, to) - last.from + 1 <= MAX_GROUP_WORDS;
    if (merged && (from <= last.to + 1 || flat[from].start - flat[last.to].end < GLUE_SEC)) {
      last.to = Math.max(last.to, to);
      last.keys++;
      last.terms.set(flat[i].term, flat[i].weight);
      last.verbatim ||= Boolean(flat[i].verbatim);
    } else if (!last || from > last.to) {
      groups.push({
        from,
        to,
        keys: 1,
        terms: new Map([[flat[i].term, flat[i].weight]]),
        verbatim: Boolean(flat[i].verbatim),
      });
    } else {
      last.keys++; // слово попало в уже набранную группу, но растить её нельзя
      last.terms.set(flat[i].term, flat[i].weight);
      last.verbatim ||= Boolean(flat[i].verbatim);
    }
  }
  if (!groups.length) return { spans: [], share: 1, source };

  // 3. Оставляем самые насыщенные, пока укладываемся в долю чанка. Плотность, а
  //    не абсолютное число попаданий: длинная фраза с двумя ключевыми словами
  //    менее показательна, чем короткая с теми же двумя. Считаем по РАЗНЫМ
  //    словам: десять повторов одного слова — это всё ещё одно совпадение.
  const budget = Math.max(1, Math.floor(flat.length * MAX_SHARE));
  const score = (g) => [...g.terms.values()].reduce((a, b) => a + b, 0) / Math.sqrt(g.to - g.from + 1);
  // Место, зацепившееся за ОДНО слово, — обычно случайность: в живой цитате так
  // всплывала «моделька Image Edit» из-за слова «модели» в утверждении. Если
  // есть места побогаче, одиночки не показываем вовсе.
  const rich = groups.filter((g) => g.terms.size > 1 || g.verbatim);
  // Место, где слова утверждения идут ПОДРЯД, — это пересказ именно его, и оно
  // показывается раньше любых плотных наборов разрозненных слов. Правило, а не
  // перевес в очках: иначе исход зависит от того, насколько редким оказалось
  // соседнее слово, и ломается от любой правки весов.
  const byDensity = (rich.length ? rich : groups).sort(
    (a, b) => Number(Boolean(b.verbatim)) - Number(Boolean(a.verbatim)) || score(b) - score(a)
  );
  const picked = [];
  let shown = 0;
  for (const group of byDensity) {
    const size = group.to - group.from + 1;
    if (picked.length >= MAX_SPANS) break;
    if (shown + size > budget && picked.length) break;
    picked.push(group);
    shown += size;
  }
  picked.sort((a, b) => a.from - b.from);

  const share = shown / flat.length;
  if (share > POINTLESS_SHARE) return { spans: [], share: 1, source };
  return {
    spans: picked.map((g) => ({ start: flat[g.from].start, end: flat[g.to].end, keys: g.keys })),
    share,
    source,
  };
}

/** Номер промежутка, в который попало слово, или -1 (границы включительно). */
export function spanAt(start, spans) {
  for (let i = 0; i < spans.length; i++) {
    if (start >= spans[i].start && start <= spans[i].end) return i;
  }
  return -1;
}

/** Попадает ли слово в один из промежутков. */
export function inSpans(start, spans) {
  return spanAt(start, spans) >= 0;
}
