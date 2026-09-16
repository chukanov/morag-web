// Панель управления звуком в шапке: появляется, когда что-то играет.
//
// Зачем: звук намеренно не глохнет при уходе с читалки — человек мог включить
// запись и пойти читать ответы. Но тогда управление оставалось на странице,
// с которой ушли: чтобы поставить на паузу, приходилось искать её обратно.
// Панель живёт в шапке, поэтому доступна отовсюду и знает, куда вернуть.
import { $, el, fmt } from "./dom.js";
import * as player from "./player.js";

const PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const PAUSE =
  '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
/**
 * @param {object} opts onGoto(recordId, sec) — открыть запись на звучащей секунде
 */
export function mountMiniPlayer({ onGoto } = {}) {
  const host = $("#now-playing");
  if (!host) return;

  const toggle = el("button", { class: "np-btn np-play", html: PLAY, title: "Слушать", "aria-label": "Слушать" });
  const label = el("button", { class: "np-title", title: "Вернуться к звучащему месту" });
  const clock = el("span", { class: "np-time", text: "0:00" });

  host.replaceChildren(toggle, el("span", { class: "np-meta" }, label, clock));
  host.hidden = true;

  toggle.addEventListener("click", () => {
    const s = player.state();
    if (s.playing) player.pause();
    else if (s.url) player.play(s.url, { at: s.time, owner: s.owner });
  });
  label.addEventListener("click", () => {
    const s = player.state();
    if (s.record) onGoto?.(s.record, Math.floor(s.time));
  });

  // Состояние прилетает каждый кадр — пишем только изменившееся.
  let wasPlaying = null;
  let lastClock = "";
  const update = (s) => {
    // Панель нужна ровно тогда, когда запись ЗАПУСКАЛИ, а её собственный плеер не на экране:
    // в читалке и в чате управление уже есть, и вторая копия в шапке только сбивала бы с толку.
    //
    // ⚠️ Признак — владелец, а не адрес. По адресу это ломалось с появлением `player.prime`:
    // читалка теперь готовит запись заранее (ради первого клика по слову), адрес есть сразу,
    // и панель вылезала в шапку на странице, где ничего не звучало. Владелец же появляется
    // только у настоящего пуска — его ставит `play`, и никто больше.
    host.hidden = !s.owner || ownerOnScreen(s);
    if (host.hidden) return;

    if (s.playing !== wasPlaying) {
      wasPlaying = s.playing;
      toggle.innerHTML = s.playing ? PAUSE : PLAY;
      toggle.title = s.playing ? "Пауза" : "Слушать";
    }
    host.classList.toggle("playing", s.playing);
    const time = s.duration ? `${fmt(s.time)} / ${fmt(s.duration)}` : fmt(s.time);
    if (time !== lastClock) {
      clock.textContent = time;
      lastClock = time;
    }

    // Подпись даёт читалка; идентификатор записи («2026-03-12-kafka») в шапке не показываем —
    // он длинный и человеку ничего не говорит.
    const name = s.title || "Запись";
    if (label.textContent !== name) label.textContent = name;
    label.disabled = !s.record;
  };

  player.subscribe(update);
  // Ушли со страницы — плеер, с которого запускали, пропал с экрана, и панель
  // должна появиться сама, не дожидаясь следующего кадра звука.
  // ⚠️ Событие своё, а не `hashchange`: маршрутизация давно на путях, и слушатель хеша не
  // срабатывал НИКОГДА. Оба слушателя нужны: `morag:navigate` — на переходы внутри
  // приложения, `popstate` — на кнопку «назад» браузера.
  addEventListener("morag:navigate", () => update(player.state()));
  addEventListener("popstate", () => update(player.state()));
}

/**
 * Виден ли сейчас тот плеер, которым запустили запись.
 *
 * Считаем по маршруту, а не по видимости узла: читалка живёт на своём адресе,
 * карточки-моменты — в чате, и этого достаточно, чтобы не гадать про прокрутку.
 */
function ownerOnScreen(s) {
  const owner = typeof s.owner === "string" ? s.owner : "";
  // ⚠️ Читаем ПУТЬ, а не хеш. Адреса переехали на пути ещё у подкаста, а эта проверка осталась
  // хешевой — и всегда возвращала «нет». Панель в шапке показывалась прямо в читалке, рядом с
  // её собственными кнопками: две полосы перемотки на одном экране, ровно то, ради чего
  // проверка и написана. Id в адресе закодирован (`buildPath`), поэтому сравниваем с кодированным.
  const path = location.pathname;
  if (owner.startsWith("rd:")) return path.includes(`/rec/${encodeURIComponent(owner.slice(3))}`);
  if (owner.startsWith("m")) {
    // Карточка-момент живёт в чате — а с 15.09 и на странице записи, цитаты которой играет.
    // ⚠️ Адрес диалога — `/<слаг>/chat/<id>` (с 11.09), а проверка `endsWith("/chat")` его не
    // узнавала: панель вылезала в шапку прямо над играющей карточкой.
    if (/(^|\/)chat(\/|$)/.test(path)) return true;
    return Boolean(s.record) && path.includes(`/rec/${encodeURIComponent(s.record)}`);
  }
  return false;
}
