// Сборка приложения: бренд и тема приезжают с сервера, вёрстка — общая.
import { $, el, fmt, reducedMotion, toast, countOf, copyLink } from "./ui/dom.js";
import { initTheme, applyThemeTokens } from "./ui/theme.js";
import { getSite, getCorpora, useCorpus, getRecords, getMe } from "./api.js";
import { useSession, can, SIGNIN } from "./session.js";
import { renderSignin } from "./signin.js";
import { mountUserMenu } from "./ui/user.js";
import { createRouter, slugFromPath } from "./router.js";
import { renderHub } from "./hub.js";
import { renderVoices } from "./voices.js";
import { renderCalendar } from "./calendar.js";
import { createChat } from "./chat/controller.js";
import { saveDialogMd } from "./chat/save.js";
import { listDialogs, deleteDialog, formatWhen } from "./chat/history.js";
import { renderRecords, forgetScroll, collapseFilters } from "./records/list.js";
import { renderReader, leaveReader, focusAsk } from "./records/reader.js";
import { TITLE_FALLBACK } from "./ui/brand.js";
import { measureTopbar } from "./ui/topbar.js";
import { initLogo, showMark } from "./ui/logo.js";
import { mountMiniPlayer } from "./ui/miniplayer.js";
import { initAnalytics, pageview, track } from "./ui/analytics.js";
const ARROW =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>';

initTheme();

// ⚠️ Высота шапки — в переменную стилей, по факту (`ui/topbar.js`): ровно от разъезда
// «константа в двух местах» предостерегает комментарий над той же константой в читалке.
measureTopbar();
addEventListener("resize", measureTopbar);
// Знак набран моноширинным шрифтом: до его подъезда высота шапки другая.
document.fonts?.ready.then(measureTopbar);

// Знак в шапке живёт в `ui/logo.js`; слушатели наведения вешаются один раз, знак под ними
// монтируется, когда приедет бренд (пространства — здесь, формы входа — в signin.js).
initLogo();

// Прокрутку восстанавливаем сами. Браузер (заметнее всех Safari) возвращает
// прежнюю позицию уже ПОСЛЕ того, как читалка навелась на секунду из ссылки, —
// и ссылка «на конкретное место» открывается не там.
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

// Порядок загрузки: сначала узнаём, В КАКОМ пространстве мы находимся, и только потом просим
// его данные. Слаг обязан быть известен ДО первого запроса — иначе сервер отдаст пространство
// по умолчанию, и страница одного раздела молча покажет записи другого.
// Форма входа — до всего: на ней нет ни пространства, ни `me` (401 без сессии увёл бы на
// неё же — кольцо). Сервер сюда и приводит страницу без сессии, с адресом возврата.
const onSignin = location.pathname === SIGNIN;
let slug = onSignin ? "" : slugFromPath(location.pathname);
let hubData = null;
// Кто вошёл и что ему можно — раньше корпуса: от этого зависит, показывать ли карандаш и
// открывать ли карточку голоса. Без входа сервер отдаёт пустую личность с теми же флагами.
const me = onSignin ? null : await getMe().catch(() => null);
useSession(me);
let site = slug ? await getSite(slug).catch(() => null) : null;

if (!site && !onSignin) {
  hubData = await getCorpora().catch(() => null);
  const spaces = hubData?.corpora || [];
  if (spaces.length === 1) {
    // Пространство одно — витрины нет и быть не должно: экран со списком из одной карточки
    // это лишний шаг. Так же поедет любой корпус с единственным разделом.
    const rest = slug ? location.pathname.replace(/^\/[^/]+/, "") : location.pathname;
    slug = spaces[0].slug;
    site = await getSite(slug).catch(() => null);
    if (site) history.replaceState(history.state, "", `/${slug}${rest === "/" ? "" : rest}`);
  } else if (slug) {
    // Слаг в адресе есть, а пространства такого нет: ссылку могли обрезать при пересылке.
    // Показываем витрину, а не пустоту и не чужой раздел.
    history.replaceState(history.state, "", "/");
    slug = "";
  }
}
if (site) {
  useCorpus(site.slug, site.media_base);
  applySite(site);
} else if (hubData?.hub) {
  // Верстак голосов живёт вне пространства, и своего бренда у него нет: берём сайтовый,
  // иначе страница открывается без темы и без названия в шапке.
  applyThemeTokens(hubData.hub.theme || {});
  document.title = hubData.hub.title || "Записи";
}
initAnalytics(site?.analytics || {});
mountUserMenu(me);
// Знак — из бренда пространства, у витрины — её; форма входа получит свой от сервера входа.
if (!onSignin) showMark(site?.brand || hubData?.hub || {});

/** Ссылка на место в записи — то, чем делятся. Слаг корпуса в ней обязателен. */
const shareMoment = (recordId, sec) =>
  copyLink(router.href({ view: "reader", id: recordId, sec }), "Ссылка на момент скопирована");

const router = createRouter(
  {
    hub: async () => {
      hubData = hubData || (await getCorpora().catch(() => null));
      if (hubData) renderHub(hubData, { mount: $("#hub-list") });
    },
    voices: (id) => renderVoices(id).catch(fail),
    signin: () => renderSignin().catch(fail),
    // Календарь выступлений: год — в адресе, чтобы им можно было поделиться.
    calendar: (year) =>
      renderCalendar(year, {
        onOpen: (id) => router.go({ view: "reader", id }),
        // Год — `replace`: адресом делятся, а историю он не засоряет (см. router.back).
        onYear: (y) => router.go({ view: "calendar", id: y }, { replace: true }),
      }).catch(fail),
    // Главная И ЕСТЬ список записей — и вход в разговор. Старые адреса `/<слаг>/records`
    // и `/<слаг>/chat` (бывший пустой экран чата) сюда доезжают — приводим к каноническому,
    // сохранив фильтры.
    home: () => {
      if (/\/(records|chat)$/.test(location.pathname)) {
        history.replaceState(history.state, "", location.pathname.replace(/\/(records|chat)$/, "") + location.search);
      }
      // С диалога возвращаемся К ПОЛЮ, а не в середину списка: память прокрутки помнит
      // место, откуда открыли запись, и утащила бы поле вопроса за экран.
      if (prevView === "chat") {
        forgetScroll();
        scrollTo(0, 0);
      }
      refreshPastButton();
      refreshHints(); // каждый заход — свои три темы: так виден охват корпуса
      return renderRecords({ onOpen: (id) => router.go({ view: "reader", id }) }).catch(fail);
    },
    // Диалог по адресу. Свой текущий — ничего не делаем (сюда же приводит отправка с
    // главной, и лента уже наша); чужой id — поднимаем из браузера; нет такого — заглушка.
    // ⚠️ На «не найден» НЕ сбрасываем разговор: идущий ответ другой сессии остаётся жить,
    // а лента с композером прячутся стилями, чтобы под чужим адресом нельзя было дописать.
    // ⚠️ Без scrollTo(0,0): восстановленный диалог сам мотает к последнему ходу.
    chat: (id) => {
      const missing = id !== chat.sessionId && !chat.open(id);
      document.body.toggleAttribute("data-chat-missing", missing);
      if (!missing) $("#q")?.focus({ preventScroll: true });
    },
    reader: (id, sec) =>
      renderReader(id, sec, {
        onBack: () => router.go({ view: "home" }),
        onAsk: (recordId, at) => askAboutMoment(recordId, at),
        onShare: shareMoment,
        // Права — подсказки интерфейсу, рубеж на сервере: карандаш — право `edit`, карточка
        // голоса и «починить везде» — `voices` (имя и правило действуют на весь корпус).
        editing: can("edit"),
        fixes: can("voices"),
        // Вопрос к этой записи: кнопки из конфига корпуса; снятая галочка уводит в общий чат.
        // Поиск у пространства выключен — панели нет вовсе (сервер и так ответил бы отказом).
        presets: site?.chat_enabled === false ? null : site?.ask_presets || {},
        onAskCorpus: startChat,
        onOpenRecord: (recordId, at) => router.go({ view: "reader", id: recordId, sec: at }),
      }).catch(fail),
  },
  { getSlug: () => site?.slug || null }
);

// Уходя с читалки, снимаем её подписки. Звук при этом НЕ глушим намеренно:
// человек мог включить запись и пойти читать ответы — обрывать его грубо.
// Следим за сменой адреса, а не хеша: маршрутизация переехала на пути.
const watchLeaveReader = () => {
  if (!/\/rec\//.test(location.pathname)) leaveReader();
};

// Откуда пришли — знает только сам переход: событие уходит ДО вызова обработчика экрана,
// поэтому внутри обработчика `prevView` — это ещё прежний экран.
let prevView = null;
let curView = null;
addEventListener("morag:navigate", (event) => {
  prevView = curView;
  curView = event.detail.view;
});
// Одностраничник: переходы между экранами браузер счётчику не показывает —
// шлём сами, из того же события навигации.
addEventListener("morag:navigate", () => pageview());
addEventListener("popstate", watchLeaveReader);
addEventListener("morag:navigate", watchLeaveReader);

const chat = createChat({
  onOpenRecord: (id, sec) => router.go({ view: "reader", id, sec: Math.round(sec || 0) }),
  onShareMoment: shareMoment,
  // Только факт вопроса и корпус — ни текста, ни ответа.
  onSend: () => track("ask", site?.slug || ""),
  // Ответ доехал, а человек уже вернулся на главную — кнопка прошлых разговоров обязана
  // появиться без перезагрузки.
  onSaved: () => curView === "home" && refreshPastButton(),
});

// Вопрос с главной: новый разговор → его адрес → отправка. ⚠️ Порядок «перейти, потом
// отправить» не случаен: заставка темы меряет ширину ленты, а скрытый экран ширины не имеет —
// сцена собралась бы по ширине окна и обрезалась. Сброс и отправка идут в одном тике —
// контроллер это переживает (см. номер прогона в createChat).
{
  const form = $("#ask-form");
  const input = $("#ask-q");
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    startChat(question);
  });
  // Поле не на всю ширину рамки: клик по лупе и полям слева тоже должен ставить курсор.
  form?.addEventListener("click", (event) => {
    if (!event.target.closest("button")) input.focus();
  });
}

// Примеры вопросов под полем — ТЕКСТОМ с пунктиром, не чипами: чипы путались с рядами фильтров
// ниже (решение владельца 11.09). Темы берём из свежих записей, а не из списка в конфиге:
// конфиг стареет молча, а доклады приезжают сами. Заодно это честная витрина — видно, о чём
// корпус на самом деле и что он живой. Список из site.yml остаётся запасным: у нового корпуса
// записей нет.
const FALLBACK_EXAMPLES = site?.brand?.examples || [];
let topicPool = null;

async function refreshHints() {
  if (topicPool === null) {
    try {
      const data = await getRecords();
      const records = [...data.records]
        .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))
        .slice(0, 12); // только свежие: подсказка про позапрошлый год выглядит пылью
      // темы у доклада — это его теги, а если их нет, то заголовок
      topicPool = records.flatMap((e) =>
        [...(e.tags || []), e.title]
          .map((topic) => String(topic).trim())
          // слишком короткие («ИИ») бессмысленны как вопрос, слишком длинные не влезают в строку
          .filter((topic) => topic.length >= 10 && topic.length <= 52)
      );
    } catch {
      topicPool = [];
    }
  }
  const picked = [];
  const pool = [...topicPool];
  while (picked.length < 3 && pool.length) {
    picked.push(...pool.splice(Math.floor(Math.random() * pool.length), 1));
  }
  // Формулировки разные не для красоты: одинаковое «Что говорили про…» трижды читается как
  // один вопрос с подстановкой и ничему не учит. Разные вопросы показывают, ЧТО вообще можно
  // спрашивать — факт, спор, суть, участников. Все шаблоны обязаны звучать естественно с любой
  // темой в кавычках: темы — это обрывки заголовков, и «Почему важно X?» на них ломается.
  const shapes = [
    (topic) => `Что говорили про «${topic}»?`,
    (topic) => `Расскажи про «${topic}»`,
    (topic) => `В чём суть «${topic}»?`,
    (topic) => `Какие мнения про «${topic}»?`,
    (topic) => `Кто что сказал про «${topic}»?`,
    (topic) => `Чем интересно «${topic}»?`,
  ];
  const shuffled = [...shapes];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  const hints = picked.map((topic, i) => shuffled[i % shuffled.length](topic));
  renderHints(hints.length ? hints : FALLBACK_EXAMPLES);
}

/** Пример ПРЕДЗАПОЛНЯЕТ поле, а не отправляет: вопрос можно доправить. Строка остаётся —
 *  выбрать другую тему должно быть так же просто, как первую. */
function renderHints(examples) {
  const line = $("#ask-hints");
  const input = $("#ask-q");
  if (!line || !input) return;
  line.hidden = !examples.length;
  const nodes = [];
  examples.forEach((text, i) => {
    if (i) nodes.push(" · ");
    const hint = el("button", { class: "hint", type: "button", text });
    hint.addEventListener("click", () => {
      input.value = text;
      input.focus();
      input.setSelectionRange(text.length, text.length);
    });
    nodes.push(hint);
  });
  line.replaceChildren(...nodes);
}

// Кнопка «Спросить» в шапке: вход к вопросу со страниц читалки и голосов. Ссылки на
// моменты ведут в читалку, и без неё пришедший по ссылке видел только расшифровку — путь к
// главному начинался с догадки. Ведёт на главную, к полю; там и на диалоге её прячет CSS по
// `data-view` (роутер ставит его на body).
$("#go-ask")?.addEventListener("click", () => {
  // На странице записи «Спросить» открывает вопрос К НЕЙ (владелец, 14.09): человек уже здесь,
  // и уводить его на главную ради вопроса про то, что он читает, — лишний ход. Панели нет
  // (поиск выключен, запись не открылась) — ведём себя как раньше.
  if (focusAsk()) return;
  forgetScroll();
  router.go({ view: "home" });
  scrollTo(0, 0);
  $("#ask-q")?.focus({ preventScroll: true });
});

$("#save-md")?.addEventListener("click", () =>
  saveDialogMd(chat.turns, { corpus: site?.brand?.title || "", topic: chat.topic })
);

/** Список прошлых разговоров: заголовок, когда был, удаление. Живёт в попапе у поля
 *  вопроса на главной: прошлый разговор нужен ровно в момент, когда собираешься спрашивать.
 *  В шапке такая выпадашка уже была и убрана как лишняя на всех прочих страницах. */
function renderDialogList(container, { cap, empty, onDone, onChange } = {}) {
  const items = listDialogs();
  if (!items.length) {
    if (empty) container.replaceChildren(el("div", { class: "dialogs-empty", text: empty }));
    else container.replaceChildren();
    return items.length;
  }
  // ⚠️ Нативный replaceChildren null не пропускает — рисует текст «null» (см. reader.js).
  container.replaceChildren(
    ...[cap ? el("div", { class: "dialogs-cap", text: cap }) : null].filter(Boolean),
    ...items.map((item) => {
      const del = el("button", { class: "d-del", text: "×", title: "Удалить" });
      del.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteDialog(item.id);
        // Удалили последний — владелец списка решает, что показывать (кнопку прячет он).
        if (onChange) onChange();
        else renderDialogList(container, { cap, empty, onDone });
      });
      const row = el(
        "button",
        { class: "d-item" },
        el("span", { class: "d-title", text: item.title }),
        el("span", { class: "d-when", text: formatWhen(item.ts) }),
        del
      );
      // Открывает обработчик экрана диалога — по адресу, как и любую ссылку на разговор.
      row.addEventListener("click", () => {
        onDone?.();
        router.go({ view: "chat", id: item.id });
      });
      return row;
    })
  );
  return items.length;
}

// Попап прошлых разговоров у поля. Пусто — кнопки нет вовсе, а не «Пока пусто».
const pastBtn = $("#past-btn");
const pastBox = $("#past");
function showPast(on) {
  if (!pastBtn || !pastBox) return;
  pastBox.hidden = !on;
  pastBtn.setAttribute("aria-expanded", String(on));
}
function refreshPastButton() {
  if (!pastBtn || !pastBox) return;
  const count = renderDialogList(pastBox, {
    cap: "Продолжить прошлый разговор",
    onDone: () => showPast(false),
    onChange: refreshPastButton,
  });
  pastBtn.hidden = !count;
  if (!count) showPast(false);
}
pastBtn?.addEventListener("click", () => showPast(pastBox.hidden));
// Закрытие: клик снаружи и Esc. Клик по самой кнопке снаружи не считается — иначе попап
// закрывался бы здесь и тут же открывался её собственным обработчиком.
document.addEventListener("click", (event) => {
  if (pastBox && !pastBox.hidden && !event.target.closest(".dialogs-wrap")) showPast(false);
});
addEventListener("keydown", (event) => {
  if (event.key === "Escape" && pastBox && !pastBox.hidden) showPast(false);
});

// Знак в шапке — «главная с чистого листа»: развёрнутые «ещё фильтры» сворачиваются
// (владелец, 15.09). Фаза захвата — раньше роутера, чтобы список перерисовался уже свёрнутым.
document.addEventListener("click", (event) => {
  if (event.target.closest?.(".logo[data-go=\"home\"]")) collapseFilters();
}, true);

// «Назад» с диалога — на главную. Разговор НЕ сбрасывается: он сохранён и стоит первым в
// списке у поля, а новый начинается сам, когда зададут следующий вопрос.
$("#chat-back")?.addEventListener("click", (event) => {
  event.preventDefault();
  router.go({ view: "home" });
});

mountMiniPlayer({ onGoto: (id, sec) => router.go({ view: "reader", id, sec }) });

router.start();

function applySite(data) {
  const brand = data.brand || {};
  applyThemeTokens(data.theme || {});

  // сайт — общий (лого в шапке), корпус — то, что мы сейчас смотрим: его название в заголовке
  // вкладки и на лендинге, крошки в шапке нет (владелец, 14.09)
  document.title = brand.title || TITLE_FALLBACK;

  const about = $("#about");
  if (about && brand.about) {
    // Описание берём разметкой как есть: его пишет владелец в site.yml, а это
    // такой же доверенный файл, как код. Для текста от посетителя или от модели
    // правило прежнее — только узлы, никакого innerHTML (см. chat/md.js).
    about.innerHTML = withCount(brand.about, data.records_count);
    // Внешние ссылки уводим в новую вкладку, чтобы не терять открытый чат.
    // Делаем это здесь, а не переписыванием в конфиге: забыть легко, а эффект
    // неприятный — человек уходит с сайта на полуслове.
    for (const link of about.querySelectorAll('a[href^="http"]')) {
      link.target = "_blank";
      link.rel = "noopener";
    }
  }
}

/**
 * «Спросить про это место»: уводим в чат, а запись и секунду держим при себе —
 * следующий вопрос уйдёт вместе с ними. Окно реплик вокруг соберёт СЕРВЕР из
 * своего индекса: слать его с клиента — и лишний вес, и дыра для подмены.
 */
/** Начать НОВЫЙ разговор по всему корпусу. Один помощник на все входы: поле на главной и
 *  снятая галочка «только у этой записи» на странице записи. Порядок важен: сначала переход
 *  (заставка темы меряет ширину ленты), потом отправка. */
function startChat(question) {
  chat.reset();
  forgetScroll();
  scrollTo(0, 0);
  router.go({ view: "chat", id: chat.sessionId });
  chat.send(question);
}

function askAboutMoment(recordId, sec) {
  router.go({ view: "chat", id: chat.sessionId });
  chat.armContext({ record_id: recordId, sec }, `Что спросить про это место (${fmt(sec)})?`);
  toast("Спрашивайте — я помню, о каком месте речь");
}

function setText(selector, value) {
  const node = $(selector);
  if (node && value) node.textContent = value;
}

/**
 * Подставляет в бренд-строки живое число записей — в конфиге число писать
 * не надо, появился новый доклад, и текст не соврал.
 *
 * Плейсхолдеров два, потому что падежа тоже два и одной формой не обойтись:
 *   {записей}      счётная форма при числе      «12 записей», «22 записи»
 *   {записей-род}  родительный после предлога   «любую из 12 записей»,
 *                                               но «любую из 21 записи»
 */
function withCount(text, count) {
  if (!text) return text;
  const gen = count ? countOf(count, "записи", "записей", "записей") : "записей";
  const nom = count ? countOf(count, "запись", "записи", "записей") : "записей";
  return text.replace(/\{записей-род\}/g, gen).replace(/\{записей\}/g, nom);
}

function fail(error) {
  console.error(error);
  toast("Не удалось загрузить данные");
}
