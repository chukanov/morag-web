// Тонкая обёртка над нашим BFF.
import { readFrames } from "./sse.js";
import { loginPath, SIGNIN } from "./session.js";

// Пространство, за данными которого мы ходим, и откуда брать видео. Ставится ОДИН раз при
// запуске (`useCorpus`) и живёт до перезагрузки страницы.
//
// ⚠️ Почему модульная переменная, а не параметр каждого вызова: слаг нужен девяти вызовам в
// шести файлах, и достаточно забыть один, чтобы страница одного раздела молча показала данные
// другого. Ровно так это и было сломано у подкаста — фронт не слал слаг вообще, и любой адрес
// отдавал корпус по умолчанию. Переход между пространствами — полная перезагрузка страницы,
// поэтому значение за время жизни документа не меняется и гонки быть не может.
let SLUG = "";
let MEDIA_BASE = "";

/** Назначить пространство. Зовётся один раз на старте, до первого запроса данных. */
export function useCorpus(slug, mediaBase = "") {
  SLUG = slug || "";
  MEDIA_BASE = mediaBase || "";
}

/** Хвост запроса со слагом. Пусто — когда пространство одно и слага нет вовсе. */
function q(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== "") search.set(key, value);
  }
  if (SLUG) search.set("slug", SLUG);
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Ошибка ответа BFF: текст — `detail` сервера (он человеческий), статус — для тех, кто
 *  различает 401/403/429. */
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Сессия протухла или её нет — на форму входа, с возвратом сюда же. Сервер на страницу без
 *  сессии отвечает редиректом сам; сюда доходит только API из уже открытого приложения.
 *  На самой форме не дёргаемся: её ручки открыты, а 401 там — неверный пароль. */
function toSignin() {
  if (typeof location === "undefined" || location.pathname === SIGNIN) return;
  location.assign(loginPath(location.pathname + location.search));
}

/** Один путь для всех запросов к BFF: JSON туда, JSON обратно, ошибка — `ApiError` с
 *  `detail` сервера. Три прежние копии (GET, POST, переименование голоса) разъезжались
 *  в том, что одна из них тело ошибки выбрасывала. */
async function request(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.ok) return response.json();
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) toSignin();
  throw new ApiError(response.status, data.detail || `${path}: ${response.status}`);
}

const getJSON = (path) => request(path);

/** Витрина: какие вообще есть пространства. Единственный запрос, который нужен главной. */
export const getCorpora = () => getJSON("/api/corpora");
/** Слаг здесь ЯВНЫЙ: это первый запрос, `useCorpus` ещё не звали. */
export const getSite = (slug) => getJSON(`/api/site${slug ? `?slug=${encodeURIComponent(slug)}` : ""}`);
export const getRecords = () => getJSON(`/api/records${q()}`);
export const getCalendar = () => getJSON(`/api/calendar${q()}`);
/** Адрес картинки бренда (обложка, портрет докладчика) — файл лежит у пространства. */
export const brandAsset = (name) =>
  `/api/brand/${String(name).split("/").map(encodeURIComponent).join("/")}${q()}`;

export const transcriptUrl = (id) => `/api/records/${encodeURIComponent(id)}/transcript.md${q()}`;
// Кадр экрана из каталога записи (обложка карточки): имя вида `slides/s003.jpg` содержит слэш —
// кодируем посегментно, как медиа и бренд, а не целиком.
export const coverUrl = (id, name) =>
  name ? `/api/records/${encodeURIComponent(id)}/frame/${String(name).split("/").map(encodeURIComponent).join("/")}${q()}` : "";
// Кадры записи для слайдшоу на карточке: имена кадров без людей и рамка обрезки обложки.
export const getFrames = (id) => getJSON(`/api/records/${encodeURIComponent(id)}/frames${q()}`);
export const slidesUrl = (id) => `/api/records/${encodeURIComponent(id)}/slides.pdf${q()}`;
/** Адрес видео. Имя из шапки записи — у перенесённых это ПУТЬ внутри архива
 * («Каталог/Подкаталог/файл.mp4», в сегментах бывают кириллица и пробелы), поэтому кодируем
 * ПОСЕГМЕНТНО: encodeURIComponent целиком превратил бы слэши в %2F, а простая склейка
 * оставила бы пробел сырым — и сервер не нашёл бы файл ни в том, ни в другом случае. */
export const mediaUrl = (name) => {
  if (!name) return "";
  const path = String(name).split("/").map(encodeURIComponent).join("/");
  // Задан адрес архива — идём прямо туда: гонять гигабайты через питон-процесс незачем, а
  // Range-запросы (перемотка) архив отдаёт сам. Не задан — раздаём локальные файлы сами.
  // ⚠️ Форма обязана совпадать с `media_url` в app/content/resolve.py: там тот же адрес
  // строится для карточек-моментов в ответе, и разойдись они — карточка молча не заиграет.
  return MEDIA_BASE ? `${MEDIA_BASE.replace(/\/$/, "")}/${path}` : `/api/media/${path}${q()}`;
};

/**
 * Слова записи с временами — для караоке и перемотки по слову.
 * Без промежутка — вся запись (читалка), с промежутком — только он (карточка).
 * `pad` — сколько реплик разговора добавить вокруг («шире» на карточке).
 */
export function getWords(id, { start, end, pad = 0 } = {}) {
  const range = start != null && end != null ? { start, end, pad: pad || "" } : {};
  return getJSON(`/api/records/${encodeURIComponent(id)}/words${q(range)}`);
}

/** Задать вопрос. Кадры отдаются по мере поступления; отмена — через AbortController. */
export async function* ask({ question, sessionId, history = [], context = null, wantTopic = false }, signal) {
  const response = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      session_id: sessionId,
      history,
      context,
      corpus: SLUG, // у каждого пространства свой мораг и своя коллекция
      want_topic: wantTopic, // тему просим явно: клиент знает, показана ли она
    }),
    signal,
  });

  if (!response.ok) {
    if (response.status === 401) toSignin();
    // 429 — это лимит, и текст к нему сервер подбирает сам: «слишком часто»,
    // «дождитесь ответа» и «на сегодня всё» — разные поводы и разные советы.
    // Показываем присланное, а своё сообщение оставляем на случай, если тела нет.
    let message =
      response.status === 503
        ? "Сейчас отвечаю другим посетителям — попробуйте через минуту."
        : "Не получилось задать вопрос. Попробуйте ещё раз.";
    if (response.status === 429) {
      message = "Слишком часто — подождите немного.";
      try {
        const body = await response.json();
        if (body?.detail) message = body.detail;
      } catch {
        /* тело не пришло или не JSON — остаётся наш текст */
      }
    }
    yield { type: "error", message };
    return;
  }
  yield* readFrames(response, signal);
}


/** Оценка ответа (👍/👎). Живёт здесь, а не в контроллере чата: тот же ответ оценивают и на
 *  странице записи, а вторая копия этих строк разъехалась бы с первой. */
export async function sendFeedback(answerId, vote) {
  if (!answerId) return;
  try {
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer_id: answerId, vote }),
    });
  } catch {
    // оценка — не то, ради чего стоит беспокоить посетителя ошибкой
  }
}

// --- голоса корпуса --------------------------------------------------------
//
// Слаг здесь НЕ нужен: `Speaker_N` — идентификатор из реестра голосов, общий на весь корпус,
// и один и тот же человек звучит в разных пространствах (79 голосов — больше чем в одном).
// Именно поэтому имя, названное один раз, применяется везде.
export const getVoices = () => getJSON("/api/voices");
export const getVoicesQueue = () => getJSON("/api/voices/queue");
/** Один голос — для правки прямо в читалке. Весь снимок (281 голос) ради одной метки не нужен.
 *  `record` — запись, из которой пришли: её кандидаты из меты идут первыми. */
export const getVoice = (id, record = "") =>
  getJSON(`/api/voices/${encodeURIComponent(id)}${record ? `?record=${encodeURIComponent(record)}` : ""}`);

/** Назвать голос. Пустое имя снимает правку — обратимость это операция, а не обещание.
 *  `claim` — «это я»: имя подставит сервер из сессии, `name` при этом не читается. */
export const renameVoice = (voice, { name = "", why = "", record = "", claim = false } = {}) =>
  request(`/api/voices/${encodeURIComponent(voice)}`, { method: "POST", body: { name, why, record, claim } });

// --- правка текста записи --------------------------------------------------
//
// Слаг не нужен и здесь: правка ложится в словари корпуса, общие на все пространства, а запись
// сервер находит по индексам всех пространств сразу.

/** Где ещё в корпусе встречается написание и какие рядом варианты. */
export const getTokens = (word) => getJSON(`/api/tokens/${encodeURIComponent(word)}`);

const send = (path, method, payload) => request(path, { method, body: payload });

/** Пачка правок абзацев одной записи. Уходит ОДНИМ запросом по выходу из режима правки:
 * иначе запись пересобиралась бы после каждого слова. */
export const saveEdits = (id, edits) =>
  send(`/api/records/${encodeURIComponent(id)}/edits`, "POST", { edits });

/** Снять все правки записи — обратимость это операция, а не обещание. */
export const dropEdits = (id) =>
  send(`/api/records/${encodeURIComponent(id)}/edits`, "DELETE");

/** «Починить везде»: правка становится правилом корпуса, поместная при этом снимается. */
export const promoteFix = (was, now, record = "") =>
  send("/api/fixes", "POST", { was, now, record });

// --- вход ------------------------------------------------------------------
//
// Слаг не нужен: вход один на весь сайт, а не на пространство. `getAuthState` и `login`
// открыты без сессии — форма их и зовёт; остальное отвечает 401 и уводит на форму.

export const getAuthState = () => getJSON("/api/auth/state");
export const getMe = () => getJSON("/api/auth/me");
export const login = (user, password) =>
  request("/api/auth/login", { method: "POST", body: { login: user, password } });
export const logout = () => request("/api/auth/logout", { method: "POST" });
