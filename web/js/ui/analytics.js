// Счётчик посещений (GoatCounter), отдаётся с нашего же домена.
//
// Почему свой, а не внешний: контур закрытый, наружу мы не ходим вовсе, да и аудитория —
// блокировщики. Запрос на чужой хост они режут, и цифры получаются занижены
// непонятно насколько. Наш счётчик first-party — считает честно.
//
// Что НЕ отправляем: тексты вопросов и ответов. Событие «задал вопрос» — это
// только факт и слаг корпуса. Содержательная аналитика у нас в журнале
// владельца: там полные данные, и они никуда не уходят.
let endpoint = null;
// Скрипт грузится асинхронно, а первый просмотр уходит сразу на старте роутера —
// без очереди САМЫЙ ПЕРВЫЙ заход терялся бы всегда, а для большинства визитов
// он единственный. Копим до готовности и высыпаем разом.
let ready = false;
const queued = [];
const send = (fn) => (ready ? fn() : queued.push(fn));

/** @param {object} cfg {endpoint, script} из конфига корпуса */
export function initAnalytics(cfg = {}) {
  // Локальную разработку не считаем: свои же заходы иначе смешаются с живыми,
  // а на localhost счётчика попросту нет и в консоль сыпались бы 404.
  const local = ["localhost", "127.0.0.1", "::1"].includes(location.hostname);
  if (!cfg.endpoint || !cfg.script || local) return;

  endpoint = cfg.endpoint;
  // no_onload: первый заход отправляем сами — роутер всё равно шлёт событие
  // навигации при старте, и без этого он бы посчитался дважды.
  window.goatcounter = { no_onload: true, no_events: true, endpoint };
  const script = document.createElement("script");
  script.async = true;
  script.src = cfg.script;
  script.dataset.goatcounter = endpoint;
  script.addEventListener("load", () => {
    ready = true;
    for (const fn of queued.splice(0)) fn();
  });
  // Не загрузился (блокировщик, обрыв) — просто забываем накопленное, а не
  // держим его в памяти вечно.
  script.addEventListener("error", () => queued.splice(0));
  document.head.append(script);
}

/**
 * Просмотр экрана.
 *
 * Секунду из адреса читалки отбрасываем: `/rec/x/1234` и `/rec/x/1250` — это одна и та же
 * запись, а как отдельные строки они бы засорили отчёт тысячей почти одинаковых путей. Какой
 * момент открывали, при нужде видно по журналу. (`/ep/` — форма подкаста, оставлена на случай
 * общего счётчика.)
 * Идентификатор диалога отбрасываем по той же причине — и ещё потому, что это приватный
 * адрес чужого разговора: счётчику знать его незачем.
 */
export function pageview(path = location.pathname) {
  if (!endpoint) return;
  const clean = path
    .replace(/^(\/[^/]+\/(?:rec|ep)\/[^/]+)\/\d+$/, "$1")
    .replace(/^(\/[^/]+\/chat)\/[^/]+$/, "$1");
  send(() => window.goatcounter?.count?.({ path: clean, title: document.title }));
}

/** Событие без текста: только факт и корпус. */
export function track(name, slug = "") {
  if (!endpoint) return;
  send(() =>
    window.goatcounter?.count?.({
      path: slug ? `${name}/${slug}` : name,
      title: name,
      event: true,
    })
  );
}
