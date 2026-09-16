// Субтитры к видео — дорожка WebVTT, собранная из пословных времён расшифровки.
//
// Почему дорожка, а не свой слой поверх кадра (решение владельца 10.09): дорожку рисует сам
// браузер, поэтому она работает ВЕЗДЕ — в полном экране, в картинке-в-картинке и на iPhone, где
// полноэкранным становится сам `<video>` и ничего чужого поверх него не видно. Взамен теряем
// подсветку звучащего слова: `::cue` умеет мало. Караоке остаётся в тексте под кадром.
//
// Файла нигде не появляется: дорожка собирается в памяти и отдаётся через blob-адрес.
//
// ⚠️ Реплика — НЕ титр. У нас реплика это абзац на 24-43 секунды (замерено на живой записи), и
// целиком в две строки внизу кадра она не влезает никогда. Поэтому режем по словам, а границы
// выбираем там, где их выбрал бы человек: конец предложения, потом длинная пауза, и только потом
// «кончилось место».

const MAX_CHARS = 84; // две строки по ~42 знака — столько браузер показывает, не заслоняя кадр
const MAX_SEC = 6; // дольше титр держать незачем: глаз уже прочитал
const GAP = 0.8; // с — пауза, на которой резать естественно
const MIN_SEC = 1.2; // короче — мелькает и раздражает сильнее, чем помогает
const MAX_WORD = 48; // растянутый звук — один токен на сотни знаков, в кадре это стена

/** Растянутый звук расшифровка отдаёт ОДНИМ словом: «Ры-и-и-и-…» на 200 знаков (живой случай,
 *  запись концерта). В тексте под кадром оно уместно, в титре — заслоняет всё; показываем начало. */
const trim = (word) => (word.length > MAX_WORD ? `${word.slice(0, MAX_WORD - 1).trimEnd()}…` : word);

/** Конец предложения — и НЕ сокращение: «т.е.» тоже носит точку. */
const ENDS = /[.!?…]["»)]?$/;
const isSentenceEnd = (word, next) =>
  ENDS.test(word) && (!next || /^[«"(]?[А-ЯЁA-Z0-9]/.test(next));

/** Имя говорящего, которое можно показать. `Speaker_314` — идентификатор реестра голосов,
 *  посетителю он не говорит ничего, и в кадре ему делать нечего. */
const nameOf = (turn) => {
  const name = String(turn?.speaker || "").trim();
  return /^Speaker_\d+$/.test(name) ? "" : name;
};

/**
 * Титры из реплик. Каждый — `{start, end, text}`, секунды.
 *
 * ⚠️ Титр НИКОГДА не переходит из реплики в реплику: там меняется говорящий, и склеенная фраза
 * приписала бы одному слова другого — ровно та ошибка, которую мы запрещаем и агенту.
 */
export function cues(turns, { maxChars = MAX_CHARS, maxSec = MAX_SEC, gap = GAP } = {}) {
  const out = [];
  let prevName = "";
  for (const turn of turns || []) {
    const words = (turn.words || []).filter((w) => w && String(w[0]).trim());
    if (!words.length) continue;
    const name = nameOf(turn);
    // Имя ставим, только когда говорящий СМЕНИЛСЯ: повторять его в каждом титре — шум.
    let prefix = name && name !== prevName ? `${name}: ` : "";
    prevName = name || prevName;

    let buf = [];
    let start = null;
    const flush = (end) => {
      if (!buf.length) return;
      out.push({ start, end, text: prefix + buf.join(" ") });
      prefix = "";
      buf = [];
      start = null;
    };

    for (let i = 0; i < words.length; i += 1) {
      const [text, from, to] = words[i];
      const word = trim(String(text).trim());
      if (start == null) start = Number(from) || 0;
      buf.push(word);
      const end = Number(to) || Number(from) || start;
      const next = words[i + 1];
      const пауза = next ? (Number(next[1]) || 0) - end : Infinity;
      const длина = (prefix + buf.join(" ")).length;
      const долго = end - start >= maxSec;
      const близко = длина >= maxChars;
      const конецФразы = isSentenceEnd(word, next && String(next[0]));
      // Порядок условий и есть правило: сначала смысловая граница, потом вынужденная.
      if (!next || конецФразы || пауза >= gap || близко || долго) flush(end);
    }
    flush(Number(words.at(-1)[2]) || Number(words.at(-1)[1]) || 0);
  }
  return stretch(out);
}

/** Слишком короткий титр растягиваем до `MIN_SEC` — но НЕ внахлёст со следующим:
 *  перекрытые титры браузер показывает разом, двумя этажами поверх кадра. */
function stretch(list) {
  return list.map((cue, i) => {
    const room = list[i + 1] ? list[i + 1].start : cue.end + MIN_SEC;
    const end = Math.max(cue.end, Math.min(cue.start + MIN_SEC, room));
    return { ...cue, end: Math.max(end, cue.start + 0.05) };
  });
}

const stamp = (sec) => {
  const t = Math.max(0, Number(sec) || 0);
  const h = String(Math.floor(t / 3600)).padStart(2, "0");
  const m = String(Math.floor((t % 3600) / 60)).padStart(2, "0");
  const s = String(Math.floor(t % 60)).padStart(2, "0");
  const ms = String(Math.round((t % 1) * 1000)).padStart(3, "0");
  return `${h}:${m}:${s}.${ms}`;
};

/** Текст дорожки. ⚠️ Переводы строк здесь значимы: пустая строка разделяет титры, и без неё
 *  браузер не разберёт файл вовсе — молча, без единой ошибки в консоли. */
export function toVtt(list) {
  const body = (list || [])
    .map((cue) => `${stamp(cue.start)} --> ${stamp(cue.end)}\n${cue.text}`)
    .join("\n\n");
  return `WEBVTT\n\n${body}\n`;
}

/** Адрес дорожки в памяти. Освобождать через `URL.revokeObjectURL`, иначе blob живёт до
 *  перезагрузки страницы — а записей за сессию открывают много. */
export function vttUrl(turns, options) {
  const text = toVtt(cues(turns, options));
  return URL.createObjectURL(new Blob([text], { type: "text/vtt" }));
}
