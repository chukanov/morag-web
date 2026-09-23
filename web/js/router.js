// Роутер по путям: /<slug> · /<slug>/chat/<диалог> · /<slug>/records · /<slug>/rec/<id>[/<сек>]
//
// Слаг корпуса в адресе не украшение: приложение generic, корпусов много,
// и ссылка обязана говорить, о каком корпусе речь.
//
// Секунда в адресе — это то, что делает цитату честной ссылкой: открываем не
// «запись вообще», а ровно то место, о котором речь.
//
// Почему пути, а не хеш: фрагмент сервер не видит вообще, поэтому в мессенджере любая
// ссылка разворачивалась бы в безликую карточку сайта. Красивый путь позволяет отдать
// превью с названием доклада и тайм-кодом (см. app/meta.py).
//
// ⓘ Разбор старых `#/ep/...` из подкаста снят: адреса прежнего сайта корпуса, которые придётся
// сохранить, другой формы, и редиректы для них — дело обратного прокси при миграции, не роутера.
const views = ["hub", "voices", "home", "chat", "reader", "calendar", "signin", "ingest"];
// ⚠️ Тот же список продублирован в app/config.py (RESERVED_SLUGS) — менять оба разом.
// Пространство с таким слагом перехватывало бы собственную страницу, и выглядело бы это
// как «раздел иногда не открывается».
// ⓘ Форма входа — `/signin`, а не `/login`: на общем с чужим сайтом домене `/login` часто занят
// (docs/auth.md), и проксировать его к себе значило бы отобрать у соседа.
const RESERVED = new Set(["chat", "records", "rec", "api", "voices", "calendar", "signin", "ingest"]);

/** Слаг пространства из адреса — или пусто. Нужен ДО первого запроса за данными. */
export function slugFromPath(pathname) {
  const first = pathname.split("/").filter(Boolean).map(decodeURIComponent)[0];
  return first && !RESERVED.has(first) ? first : "";
}

/** Разбор адреса. Экспортирован ради проверок: обратный разбор собранного адреса
 *  эту логику не покрывает — ошибка бывает в ПОРЯДКЕ веток, а не в форме пути. */
export function parsePath(pathname, knownSlug) {
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  let slug = null;
  if (parts.length && !RESERVED.has(parts[0])) slug = parts.shift();
  const route = { slug: slug || knownSlug || null, view: "home" };
  // ⚠️ Голоса разбираются ПЕРВЫМИ, до всех проверок про слаг. Идентификатор голоса общий на весь
  // корпус, поэтому у верстака слага нет и быть не должно — а проверка «нет слага, значит
  // витрина» стояла раньше и перехватывала его: `/voices/Speaker_165` открывал главную.
  if (parts[0] === "voices") return { ...route, slug: null, view: "voices", id: parts[1] || "" };
  // Форма входа — вне пространства, как и голоса: вход один на весь сайт.
  if (parts[0] === "signin") return { ...route, slug: null, view: "signin" };
  // Раздача приложения — тоже вне пространства: приложение одно на сайт, а не на раздел.
  if (parts[0] === "ingest") return { ...route, slug: null, view: "ingest" };
  // Ни слага в адресе, ни известного нам пространства — значит мы на витрине. У сайта с
  // единственным пространством этого не случается: слаг известен и подставляется.
  if (!route.slug) return { ...route, view: "hub" };
  if (!parts.length) return route;
  // Диалог — адрес, а не экран: у чата нет пустого состояния, туда попадают либо с вопросом
  // с главной, либо по ссылке на конкретный разговор. Голый `/chat` — старая ссылка на прежний
  // «пустой» экран, ведём её на вход. ⚠️ Не «чинить» обратно на view:"chat": показать там
  // нечего, и страница выглядела бы сломанной.
  if (parts[0] === "chat") return parts[1] ? { ...route, view: "chat", id: parts[1] } : route;
  // Календарь выступлений: год — необязательный второй сегмент.
  if (parts[0] === "calendar") return { ...route, view: "calendar", id: parts[1] || "" };
  // ⚠️ Отдельного экрана «Записи» больше нет: список живёт на главной (решение владельца
  // 10.09). Адрес оставлен рабочим — по нему уже ходили ссылки, и отдавать на нём пустоту
  // хуже, чем показать то же самое. Сам путь главная подчистит `replaceState`.
  if (parts[0] === "records") return { ...route, view: "home" };
  if (parts[0] === "rec" && parts[1]) {
    return { ...route, view: "reader", id: parts[1], sec: parts[2] ? Number(parts[2]) || 0 : 0 };
  }
  return route;
}

/** Адрес вида /<slug>/rec/2026-03-12-kafka/1234 — из него же строятся ссылки «поделиться». */
export function buildPath({ slug, view, id, sec }) {
  const base = slug ? `/${encodeURIComponent(slug)}` : "";
  if (view === "voices") return `/voices${id ? `/${encodeURIComponent(id)}` : ""}`;
  if (view === "signin") return "/signin";
  // Раздача приложения — вне пространства, как голоса: приложение одно на сайт. Со слагом
  // (`/demo/ingest`) адрес тоже работает, но им делятся, и «свой раздел» в нём — вранье.
  if (view === "ingest") return "/ingest";
  if (view === "chat") return `${base}/chat${id ? `/${encodeURIComponent(id)}` : ""}`;
  if (view === "calendar") return `${base}/calendar${id ? `/${encodeURIComponent(id)}` : ""}`;
  if (view === "reader") {
    const at = sec ? `/${Math.max(0, Math.round(sec))}` : "";
    return `${base}/rec/${encodeURIComponent(id)}${at}`;
  }
  return base || "/";
}

export function createRouter(handlers, { getSlug = () => null } = {}) {
  function show(name) {
    for (const view of views) {
      document.getElementById(`view-${view}`)?.classList.toggle("active", view === name);
    }
    document.body.setAttribute("data-view", name);
    document.querySelectorAll(".nav a").forEach((a) => {
      a.classList.toggle("on", a.dataset.nav === name);
    });
    // Кнопка-тумблер в шапке (календарь): на своём экране стоит нажатой — инвертированной —
    // и следующее нажатие возвращает назад (владелец, 15.09).
    document.querySelectorAll("[data-toggle]").forEach((b) => {
      const on = b.getAttribute("data-go") === name;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    });
  }

  // Глубина НАШЕЙ истории живёт в `history.state`: сколько переходов сделал роутер с момента
  // входа на сайт. Без неё «назад» с календаря, открытого по прямой ссылке из мессенджера,
  // выбросило бы на чужую страницу — а надо на главную. ⚠️ Всякий `replaceState` обязан
  // передавать `history.state` дальше, иначе глубина молча обнулится.
  const depth = () => (history.state && history.state.depth) || 0;

  function apply(route) {
    show(route.view);
    // Слушателям вроде читалки нужно знать об уходе с экрана. Раньше эту роль
    // играл `hashchange`, но адрес больше не в хеше — событие своё.
    dispatchEvent(new CustomEvent("morag:navigate", { detail: route }));
    // ⚠️ Параметры адреса получают ВСЕ обработчики, а не только читалка. Раньше здесь стояло
    // `handlers[route.view]?.()` без аргументов, и верстак голосов, открытый по адресу
    // `/voices/Speaker_165`, показывал весь список: идентификатор до него просто не доезжал.
    handlers[route.view]?.(route.id, route.sec || 0);
  }

  function route() {
    apply(parsePath(location.pathname, getSlug()));
  }

  /** `replace` — адрес меняется, а запись в истории нет: так ходит год календаря, иначе
   *  «назад» отматывал бы по годам вместо возврата туда, откуда пришли. */
  function go(target, { replace = false } = {}) {
    const path = typeof target === "string" ? target : buildPath({ slug: getSlug(), ...target });
    if (path === location.pathname) route();
    else if (replace) {
      history.replaceState(history.state, "", path);
      apply(parsePath(path, getSlug()));
    } else {
      history.pushState({ depth: depth() + 1 }, "", path);
      apply(parsePath(path, getSlug()));
    }
  }

  /** Назад по истории, если это ещё наш сайт; иначе — на запасной экран. */
  function back(fallback = { view: "home" }) {
    if (depth() > 0) history.back();
    else go(fallback);
  }

  const toView = (raw) => (views.includes(raw) ? { view: raw } : raw);

  addEventListener("popstate", () => apply(parsePath(location.pathname, getSlug())));
  document.addEventListener("click", (event) => {
    // `data-back="home"` — «Назад»: по истории, а без неё — на названный экран.
    const backLink = event.target.closest("[data-back]");
    if (backLink) {
      event.preventDefault();
      back(toView(backLink.getAttribute("data-back")));
      return;
    }
    const link = event.target.closest("[data-go]");
    if (!link) return;
    event.preventDefault();
    // В data-go пишем ИМЯ ВИДА, а не путь: слаг корпуса подставит buildPath, и
    // ссылка в шапке не потеряет его (у подкаста этим занимался разбор legacy-хеша).
    const raw = link.getAttribute("data-go");
    // Тумблер: уже на своём экране — это «назад» (`data-toggle` называет запасной экран).
    if (link.hasAttribute("data-toggle") && document.body.getAttribute("data-view") === raw) {
      back(toView(link.getAttribute("data-toggle") || "home"));
      return;
    }
    go(toView(raw));
  });

  // Клавиатура. Кнопка и ссылка сами превращают Enter/Space в клик, а вот блок — нет:
  // без этого крупная кликабельная область (обложка) остаётся недоступной с клавиатуры,
  // и это не мелочь — она главный вход в записи.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target?.closest?.("[data-go]");
    if (!target || target.tagName === "BUTTON" || target.tagName === "A") return;
    event.preventDefault();
    go(toView(target.getAttribute("data-go")));
  });

  return {
    start: route,
    go,
    back,
    /** Абсолютная ссылка — та, которую кладут в мессенджер. */
    href: (params) => location.origin + buildPath({ slug: getSlug(), ...params }),
  };
}
