// Один общий проигрыватель на весь сайт.
//
// Главное отличие от макета: там каждая карточка «играла» своим таймером.
// С настоящим видео так нельзя — два момента зазвучали бы одновременно,
// а мобильный Safari просто не даст создать второй активный элемент.
//
// Отличие от подкаста (откуда этот модуль приехал): здесь <video>, а его мало
// создать — его надо ПОКАЗАТЬ. Отсюда attach/detach: элемент один на приложение,
// а живёт он там, где сейчас читалка. Ушли с читалки — элемент откреплён, звук
// продолжает идти, и панель в шапке рассказывает, что играет.

const video = document.createElement("video");
// ⚠️ `metadata`, а не `none`. Первый клик по слову обязан начать играть С НУЖНОГО МЕСТА, а для
// этого браузеру нужны метаданные: без них перематывать некуда, и он честно начинает с нуля.
// Стоит это несколько десятков килобайт (заголовок файла по Range), а не файла целиком.
video.preload = "metadata";
// ⚠️ Без этого iOS уводит видео в свой полноэкранный плеер, и караоке не видно
// вовсе — а караоке здесь и есть смысл страницы.
video.playsInline = true;
// Родных кнопок нет: у читалки свои (пуск, полоса, скорость), и вторая полоса
// поверх первой только путала бы. Пуск/пауза по клику в кадр — ниже.
video.controls = false;
video.className = "vplayer";

const listeners = new Set();
let currentUrl = "";
let ownerKey = null;   // кто «владеет» проигрыванием: карточка или читалка
// Что именно звучит — нужно панели в шапке: она переживает уход со страницы,
// с которой запустили, и должна сама рассказать, что играет и куда вернуться.
let meta = { title: "", record: null };

// Скорость — свойство человека, а не конкретной записи: кто смотрит доклады на
// 1.5×, тот смотрит так всегда. Поэтому помним между заходами.
const RATE_KEY = "morag.rate";
export const RATES = [1, 1.5, 2];

function storedRate() {
  // Читаем «по возможности»: хранилища может не быть вовсе (тесты вне браузера)
  // или доступ к нему может быть закрыт. Модуль обязан грузиться в любом случае.
  try {
    const saved = Number(localStorage.getItem(RATE_KEY));
    return RATES.includes(saved) ? saved : 1;
  } catch {
    return 1;
  }
}
video.playbackRate = storedRate();

/** Элемент — наружу: читалке надо вставить его в свою липкую шапку. */
export const element = () => video;

// --- субтитры ---------------------------------------------------------------
//
// Дорожку рисует САМ браузер, поэтому она видна и в полном экране, и в картинке-в-картинке, и
// на iPhone — там, где свой слой поверх кадра не показать. Собирает её читалка из пословных
// времён (`records/subs.js`), сюда приезжает готовый blob-адрес.
let track = null;
let trackUrl = "";
const SUBS_KEY = "subs";

/** Помним выбор человека между записями: включил субтитры — они включены и на следующей. */
const subsWanted = () => {
  try {
    return localStorage.getItem(SUBS_KEY) === "1";
  } catch {
    return false; // приватный режим Safari — просто без памяти
  }
};

export function setSubtitles(url, label = "Расшифровка") {
  // ⚠️ Старый адрес освобождаем ВСЕГДА: blob живёт до перезагрузки страницы, а записей за
  // сессию открывают десятки — иначе память течёт молча.
  if (trackUrl) URL.revokeObjectURL(trackUrl);
  trackUrl = "";
  if (track) {
    track.remove();
    track = null;
  }
  if (!url) return;
  trackUrl = url;
  track = document.createElement("track");
  track.kind = "subtitles";
  track.srclang = "ru";
  track.label = label;
  track.src = url;
  video.append(track);
  // Режим ставим ПОСЛЕ вставки: до неё `track.track` ещё не существует.
  showSubtitles(isFullscreen() || subsWanted());
}

export function showSubtitles(on) {
  if (track?.track) track.track.mode = on ? "showing" : "disabled";
  emit();
}

export const subtitlesOn = () => track?.track?.mode === "showing";
export const hasSubtitles = () => Boolean(track);

/** Выбор человека — отдельно от временного включения в полном экране. */
export function wantSubtitles(on) {
  try {
    localStorage.setItem(SUBS_KEY, on ? "1" : "0");
  } catch {
    /* без памяти — но в этой сессии всё работает */
  }
  showSubtitles(on);
}

// --- во весь экран ----------------------------------------------------------
//
// ⚠️ Разворачиваем КОНТЕЙНЕР, а не сам `<video>`. Разница не косметическая: у развёрнутого
// `<video>` браузер рисует только кадр со своими кнопками, и положить поверх НИЧЕГО нельзя —
// ни своих субтитров, ни подсветки слова. Развёрнутый контейнер остаётся обычным элементом
// страницы, и всё, что в нём лежит, видно поверх кадра.
const fullEl = () => document.fullscreenElement || document.webkitFullscreenElement || null;
export const isFullscreen = () => Boolean(fullEl());

export function fullscreen(container) {
  const target = container || video.parentElement;
  if (!target) return;
  if (fullEl()) {
    (document.exitFullscreen || document.webkitExitFullscreen)?.call(document);
    return;
  }
  const request = target.requestFullscreen || target.webkitRequestFullscreen;
  if (request) {
    // Отказ (нет жеста, запрещено политикой) молчаливый: развернуть не вышло — остаёмся как были.
    Promise.resolve(request.call(target)).catch(() => {});
    return;
  }
  // ⚠️ iOS: полноэкранный режим есть ТОЛЬКО у самого `<video>`, у обычных элементов его нет
  // вовсе. Там разворачиваем кадр, и свои субтитры в нём не видны — для этого случая они
  // отдаются дорожкой `<track>`, которую браузер рисует сам.
  video.webkitEnterFullscreen?.();
}

for (const event of ["fullscreenchange", "webkitfullscreenchange"]) {
  // ⓘ Через `?.`: полноэкранный режим — свойство БРАУЗЕРА, и вне его (тесты соседних модулей
  // поднимают документ-заглушку без слушателей) модуль обязан просто грузиться, а не падать.
  document.addEventListener?.(event, () => {
    // В полном экране наш пульт остаётся на странице и не виден — отдаём родные кнопки:
    // без них нельзя ни перемотать, ни выйти иначе как клавишей.
    const full = isFullscreen();
    video.controls = full;
    // ⚠️ В полном экране расшифровки на странице не видно ВОВСЕ — там субтитры единственный
    // текст, и включаются они сами (решение владельца 10.09). На выходе возвращаем выбор
    // человека, а не оставляем включёнными: под кадром текст и так есть.
    showSubtitles(full || subsWanted());
    emit();
  });
}

/** Показать проигрыватель в контейнере страницы. Элемент один — он переезжает. */
export function attach(container) {
  if (container && video.parentNode !== container) container.append(video);
}

// Парковка проигрывателя. ⚠️ Медиаэлемент, ИЗЪЯТЫЙ ИЗ ДОКУМЕНТА, браузер по спецификации
// ставит на паузу. Пока `detach` звал `video.remove()`, обещание «ушли с читалки — звук
// продолжает идти» не работало вовсе, а тест был зелёным вхолостую: заглушка спецификацию не
// повторяла. Проверено в браузере — после ухода `document.querySelector("video")` отдавал null,
// и запись стояла. Поэтому элемент не удаляем, а уводим в невидимый угол ТОГО ЖЕ документа.
const parked = document.createElement("div");
parked.className = "vplayer-parked";

/** Убрать из вёрстки, не трогая воспроизведение: ушли с читалки — звук остался. */
export function detach() {
  const host = document.body;
  if (!host) {
    video.remove(); // документа нет (тесты вне браузера) — вести себя как раньше
    return;
  }
  if (parked.parentNode !== host) host.append(parked);
  parked.append(video);
}

export const rate = () => video.playbackRate;

export function setRate(value) {
  const next = RATES.includes(value) ? value : 1;
  video.playbackRate = next;
  // Сохранение — «по возможности»: в приватном режиме Safari запись падает,
  // и ронять из-за этого проигрывание нельзя.
  try {
    localStorage.setItem(RATE_KEY, String(next));
  } catch {
    /* обойдёмся без запоминания */
  }
  emit();
  return next;
}

/** Следующая скорость по кругу — под одну кнопку-переключатель. */
export function cycleRate() {
  return setRate(RATES[(RATES.indexOf(video.playbackRate) + 1) % RATES.length]);
}

// Перемотка, ждущая метаданных, и намерение играть после неё. Хранится ОДНА пара, а не
// очередь одноразовых слушателей: по словам кликают подряд, и каждый следующий клик должен
// ОТМЕНЯТЬ предыдущий, а не выстраиваться за ним.
let pendingAt = null;
let pendingPlay = false;

function emit() {
  const state = {
    subs: subtitlesOn(),
    hasSubs: hasSubtitles(),
    // Те же два поля, что в `state()`: кадр состояния и опрос обязаны говорить одно и то же,
    // иначе кнопка «во весь экран» знает про экран одно, а читалка — другое.
    picture: video.videoWidth > 0,
    full: isFullscreen(),
    url: currentUrl,
    owner: ownerKey,
    playing: !video.paused && !video.ended,
    time: video.currentTime,
    duration: Number.isFinite(video.duration) ? video.duration : 0,
    rate: video.playbackRate,
    title: meta.title,
    record: meta.record,
  };
  for (const fn of listeners) fn(state);
}

for (const event of ["play", "pause", "ended", "timeupdate", "seeked", "loadedmetadata", "ratechange"]) {
  video.addEventListener(event, emit);
}
video.addEventListener("error", () => {
  if (currentUrl) emit();
});
// Клик в кадр — пуск/пауза: родных кнопок нет, а видео, которое нельзя
// остановить пальцем, ощущается сломанным.
video.addEventListener("click", () => {
  if (!currentUrl) return;
  if (video.paused) video.play().catch(() => emit());
  else video.pause();
});
// Двойной клик — во весь экран, как в любом плеере. ⓘ Два клика по дороге успевают включить и
// выключить воспроизведение, то есть в сумме не меняют ничего, — отменять их не нужно.
video.addEventListener("dblclick", () => {
  if (currentUrl) fullscreen(video.parentElement);
});

// Пока идёт воспроизведение — рассылаем состояние КАЖДЫЙ КАДР, а не только по
// `timeupdate`.
//
// Замерено на подкасте (выпуск 2-31, 11 744 слова): медиана длительности слова
// 220 мс, а 57% слов короче 250 мс — то есть короче одного тика `timeupdate`,
// который браузеры шлют ~4 раза в секунду. На событиях подсветка физически не
// могла попасть в больше чем половину слов и шла ступеньками по четверти
// секунды. Покадрово точность становится ~16 мс.
//
// Цена мизерная: поиск слова двоичный (14 сравнений), а DOM трогается только
// при СМЕНЕ слова. Цикл живёт лишь во время воспроизведения — на паузе кадры
// не заказываем, чтобы не греть батарею впустую.
let frame = null;
function tick() {
  if (video.paused || video.ended) {
    frame = null;
    return;
  }
  emit();
  frame = requestAnimationFrame(tick);
}
video.addEventListener("play", () => {
  if (frame === null) frame = requestAnimationFrame(tick);
});
for (const event of ["pause", "ended"]) {
  video.addEventListener(event, () => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
  });
}

// Метаданные подъехали — доводим отложенную перемотку и только ПОСЛЕ неё пускаем звук.
video.addEventListener("loadedmetadata", () => {
  if (pendingAt == null) return;
  const at = pendingAt;
  pendingAt = null;
  try {
    video.currentTime = at;
  } catch {
    /* не смогли — пусть играет с того места, где оказалось: молчать хуже */
  }
  if (pendingPlay) {
    pendingPlay = false;
    video.play().catch(() => emit());
  }
});

/**
 * Подготовить запись к воспроизведению, не начиная его.
 *
 * ⚠️ Ради первого клика по слову, и это была настоящая поломка (правка владельца 08.09):
 * `src` ставился ТОЛЬКО внутри `play()`, поэтому первый клик приходил к пустому элементу.
 * Метаданных нет → перематывать некуда → браузер начинает с НУЛЯ, а нужное место подъезжает
 * через секунду-другую. Снаружи это выглядело как «первый клик не работает, а потом играет
 * с начала». Теперь адрес известен, пока человек читает, и к первому клику всё готово.
 *
 * Отступаем ровно в одном случае: что-то ЗВУЧИТ. Звук намеренно не глохнет при уходе с читалки
 * (человек мог включить доклад и пойти читать ответы), и подмена `src` оборвала бы его на
 * полуслове. Во всех прочих — переставляем на свою запись.
 *
 * ⚠️ Проверять «загружено ли что-нибудь» здесь НЕЛЬЗЯ, и это ловилось: после первой же записи
 * условие истинно всегда, и на странице СЛЕДУЮЩЕЙ записи плеер оставался висеть на прежней —
 * с её кадром и её длительностью. Выглядит так, будто сейчас заиграет старое видео.
 */
export function prime(url) {
  if (!url || currentUrl === url) return; // та же запись — не сбрасываем уже загруженное
  if (currentUrl && !video.paused) return; // звучит другая — не обрываем
  currentUrl = url;
  video.src = url.split("#")[0];
  meta = { title: "", record: null };
  // Прежний владелец был про ПРОШЛУЮ запись: не сняв его, панель в шапке считала бы, что
  // играет она, и предлагала бы вернуться не туда.
  ownerKey = null;
  pendingAt = null;
  pendingPlay = false;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function play(url, { at = null, owner = null, title, record } = {}) {
  if (!url) return Promise.resolve(); // запись без видео: играть нечего, но страница живёт
  if (currentUrl !== url) {
    currentUrl = url;
    // ⚠️ В `src` кладём адрес БЕЗ `#t=СЕК`. Ссылка на момент несёт media
    // fragment, и браузер честно отрабатывает его сам при загрузке метаданных —
    // затирая нашу перемотку, которая ждёт того же события. Наружу это выглядело
    // так: первый клик по слову играет с начала чанка, а повторный (запись уже
    // загружена, перематываем сразу) — с нужного места. Позицию задаём только мы.
    video.src = url.split("#")[0];
    meta = { title: "", record: null };  // сменилась запись — прежняя подпись не про неё
  }
  if (title !== undefined) meta.title = title;
  if (record !== undefined) meta.record = record;
  ownerKey = owner;

  const ready = video.readyState >= 1;
  if (at != null && ready) {
    try {
      video.currentTime = at;
    } catch {
      /* редкий случай: элемент есть, а перемотка отвергнута — играем как есть */
    }
  }

  // `play()` зовём В ЖЕСТЕ человека, даже когда сразу же ставим на паузу. Отложить его до
  // метаданных нельзя: iOS разрешает воспроизведение только из обработчика касания, и
  // отложенный вызов там просто откажет. Зато элемент, однажды пущенный жестом, разрешает
  // повторный пуск уже программно — на этом и держится приём ниже.
  const started = video.play().catch(() => {
    // автовоспроизведение могли заблокировать — состояние всё равно рассылаем
    emit();
  });

  if (at != null && !ready) {
    // Метаданных нет: перематывать нечем, и браузер заиграл бы с НУЛЯ. Глушим немедленно и
    // доиграем с нужного места, когда будет куда перематывать.
    pendingAt = at;
    pendingPlay = true;
    video.pause();
  }
  return started;
}

export function pause() {
  video.pause();
}

export function toggle(url, opts = {}) {
  const same = currentUrl === url && ownerKey === (opts.owner ?? null);
  if (same && !video.paused) {
    pause();
    return false;
  }
  // ⚠️ Возобновление того же — БЕЗ перемотки. Читалка передаёт `at` из адреса (у записи,
  // открытой из списка, это ноль), и без этой оговорки пауза с последующим пуском отматывала
  // запись в начало — на сороковой минуте доклада.
  play(url, same ? { ...opts, at: null } : opts);
  return true;
}

export function seek(seconds) {
  try {
    video.currentTime = Math.max(0, seconds);
  } catch {
    /* ignore */
  }
}

export const state = () => ({
  subs: subtitlesOn(),
  hasSubs: hasSubtitles(),
  // Кнопке «во весь экран» нечего показывать у записи без картинки (бывает и аудио).
  picture: video.videoWidth > 0,
  full: isFullscreen(),
  url: currentUrl,
  owner: ownerKey,
  playing: !video.paused && !video.ended,
  time: video.currentTime,
  duration: Number.isFinite(video.duration) ? video.duration : 0,
  rate: video.playbackRate,
  title: meta.title,
  record: meta.record,
});
