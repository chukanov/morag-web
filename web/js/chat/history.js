// Диалоги живут ТОЛЬКО в браузере гостя: ни аккаунтов, ни серверных копий.
// Сервер о них не знает — он видит лишь отдельные вопросы (и пишет свой
// приватный журнал для разбора провалов).
const KEY = "dialogs";
const MAX_DIALOGS = 20; // больше в localStorage держать незачем
const MAX_TURNS = 30;

export function newSessionId() {
  return crypto.randomUUID ? crypto.randomUUID() : `d${Date.now()}`;
}

function readAll() {
  try {
    const raw = localStorage.getItem(KEY);
    const data = raw ? JSON.parse(raw) : [];
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function writeAll(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_DIALOGS)));
  } catch {
    // приватный режим или переполнение — не повод ронять чат
  }
}

/** Список для меню: свежие сверху. */
export function listDialogs() {
  return readAll()
    .map(({ id, title, ts, turns }) => ({ id, title, ts, count: turns?.length || 0 }))
    .filter((d) => d.count)
    .sort((a, b) => b.ts - a.ts);
}

export function loadDialog(id) {
  return readAll().find((d) => d.id === id) || null;
}

/** Сохраняет/обновляет диалог целиком (ходы с цитатами — чтобы можно было вернуться). */
export function saveDialog({ id, title, turns, topic }) {
  if (!turns?.length) return;
  const list = readAll().filter((d) => d.id !== id);
  list.unshift({
    id,
    title: title || turns[0].question.slice(0, 80),
    ts: Date.now(),
    topic: topic || null,
    turns: turns.slice(-MAX_TURNS).map((t) => ({
      question: t.question,
      answer: t.answer,
      citations: t.citations || [],
    })),
  });
  writeAll(list);
}

export function deleteDialog(id) {
  writeAll(readAll().filter((d) => d.id !== id));
}

export function formatWhen(ts) {
  const date = new Date(ts);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return sameDay
    ? date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}
