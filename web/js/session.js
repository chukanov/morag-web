// Сессия на фронте: кто вошёл и что ему можно. Чистая логика без DOM — под node-тест.
//
// Правда о правах живёт на сервере (`/api/auth/me`, рубеж — 401/403 от ручек); здесь только
// подсказки интерфейсу: показывать ли карандаш, открывать ли карточку голоса. Одна модульная
// переменная, как `useCorpus` в api.js: флаг нужен читалке, верстаку голосов и шапке, и
// протаскивать `me` через все вызовы значит однажды забыть один.

export const SIGNIN = "/signin";

let ME = null;
// «Дом» сайта — куда возвращать, когда возвращать больше некуда. Не `/`: на общем с чужим сайтом
// домене корень — чужой сайт. Приходит от сервера (`home` в `me` и в `state` формы входа).
let HOME = "/";

/** Запомнить ответ `/api/auth/me` (или null, если вход выключен / не спрашивали). */
export function useSession(me) {
  ME = me || null;
  if (me?.home) HOME = me.home;
}

export function current() {
  return ME;
}

export function homePath() {
  return HOME;
}

/** Имя для подписи голоса «Это я»: в форме корпуса «Имя Фамилия», считает сервер. */
export function speakerName() {
  return ME?.speaker_name || "";
}

/** Право по имени: `can("edit")`, `can("voices")`. Без входа сервер отдаёт те же флаги по
 *  одному `editing.enabled` — фронту всё равно, откуда они. */
export function can(flag) {
  return Boolean(ME?.can?.[flag]);
}

/** Адрес формы входа с возвратом: `/signin?next=/demo/rec/x`. Главная — без хвоста. */
export function loginPath(next) {
  return next && next !== "/" ? `${SIGNIN}?next=${encodeURIComponent(next)}` : SIGNIN;
}

/** Куда вернуть после входа. Только СВОЙ относительный путь: `//evil`, `http://…`, обратный
 *  слэш (браузеры читают `/\evil` как `//evil`) и сама форма — домой (`home`). Открытый
 *  редирект через параметр адреса — классическая дыра формы входа. */
export function nextFrom(search, home = HOME) {
  const raw = new URLSearchParams(search || "").get("next") || "";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return home;
  if (raw === SIGNIN || raw.startsWith(`${SIGNIN}?`) || raw.startsWith(`${SIGNIN}/`)) return home;
  return raw;
}

/** Инициалы для кружка в шапке: «Мария Кузнецова» → «МК»; одно слово — две первые буквы;
 *  пусто — первая буква логина. */
export function initialsOf(name, login = "") {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return String(login || "?").slice(0, 1).toUpperCase();
}

const ROLE_NAMES = { viewer: "читатель", editor: "редактор", admin: "администратор" };

export function roleName(role) {
  return ROLE_NAMES[role] || role || "";
}

/** Текст ошибки входа по статусу. Сервер и так шлёт человеческий `detail`, но на 429/503 и
 *  без тела человеку нужно понятное слово, а не «не удалось (503)». */
export function errorMessage(status, detail = "") {
  if (status === 401) return "Неверный логин или пароль";
  if (status === 403) return detail || "Нет доступа к сайту";
  if (status === 429) return "Слишком много попыток — подождите пару минут";
  if (status === 503) return "Каталог недоступен, попробуйте позже";
  if (status === 404) return "Вход на этом сайте выключен";
  return detail || "Не удалось войти";
}
