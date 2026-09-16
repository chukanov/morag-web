// Перемотка по абзацам расшифровки — чистая логика без DOM (владелец, 13.09).
//
// Правило как у плеера с треками: → — в начало следующего абзаца; ← — если от начала текущего
// абзаца прошло больше `back` секунд, то в его начало, иначе — в начало предыдущего. Двойное ←
// с середины абзаца даёт «сначала в начало, потом назад», как и ожидают от кнопки «назад».
//
// Абзац — единица `record.words.json` с мереным началом (тот же список, что рисует караоке), а не
// слайд: слайды придут таймлайном (п. 9) и получат свои клавиши.

/** Индекс абзаца, в котором лежит секунда `t` (последний старт ≤ t); -1 до первого абзаца. */
export function paragraphAt(starts, t) {
  let lo = 0, hi = starts.length - 1, at = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (starts[mid] <= t) { at = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return at;
}

/** Куда перемотать по ←. `back` — допуск: ближе к началу считаем, что человек хочет назад. */
export function prevTarget(starts, t, back = 3) {
  if (!starts.length) return null;
  const i = paragraphAt(starts, t);
  if (i < 0) return starts[0];
  if (t - starts[i] > back) return starts[i];
  return starts[Math.max(0, i - 1)];
}

/** Куда перемотать по →; в последнем абзаце — никуда (null): за концом записи ничего нет. */
export function nextTarget(starts, t) {
  if (!starts.length) return null;
  const i = paragraphAt(starts, t);
  const next = starts[i + 1];
  return next === undefined ? null : next;
}

function typingIn(el) {
  const tag = el && el.tagName ? el.tagName.toLowerCase() : "";
  return tag === "input" || tag === "textarea" || tag === "select" || Boolean(el && el.isContentEditable);
}

const modified = (event) => event.altKey || event.ctrlKey || event.metaKey || event.shiftKey;

/** Клавиша уходит в перемотку, только если человек не печатает и не держит модификатор. */
export function isSeekKey(event) {
  if (modified(event)) return false;
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return false;
  return !typingIn(event.target);
}

/** Пробел — пауза/пуск (владелец, 13.09). Не с кнопки и не со ссылки: там пробел — их нажатие,
 *  и кнопка «Слушать» с фокусом сработала бы дважды. */
export function isPauseKey(event) {
  if (modified(event)) return false;
  if (event.key !== " " && event.key !== "Spacebar" && event.code !== "Space") return false;
  const el = event.target;
  const tag = el && el.tagName ? el.tagName.toLowerCase() : "";
  if (tag === "button" || tag === "a") return false;
  return !typingIn(el);
}
