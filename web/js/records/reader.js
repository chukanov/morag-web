// Читалка записи: видео, расшифровка и караоке по словам.
//
// Открывается по `/<slug>/rec/<id>/<сек>` — той самой ссылке, которой цитата в ответе
// показывает своё место. Секунда в адресе не декорация: пришли из цитаты —
// попадаем ровно туда, и в видео, и в тексте.
//
// Видео живёт в липкой шапке над текстом, а не субтитрами поверх кадра: на кадре слайды,
// и подпись поверх них нечитаема. Элемент один на всё приложение (ui/player.js) — сюда он
// приезжает, при уходе открепляется и продолжает звучать.
//
// Прокручивается ВСЯ СТРАНИЦА, а не внутренний блок: высоту вью никто не
// ограничивает, так что элемент с `overflow:auto` просто растягивался бы, и
// слежение за словом молча не работало (так и было в первой версии).
import { $, countOf, el, fmt, fmtDate, fmtDuration, toast } from "../ui/dom.js";
import { can, speakerName } from "../session.js";
import { buildKaraoke, follower } from "../ui/karaoke.js";
import { paintSection } from "../ui/theme.js";
import { vttUrl } from "./subs.js";
import { isPauseKey, isSeekKey, nextTarget, prevTarget } from "./seek.js";
import { createAskPanel } from "./ask-panel.js";
import * as player from "../ui/player.js";
import { getRecords, getWords, transcriptUrl, slidesUrl, mediaUrl,
         saveEdits, promoteFix, getTokens, getVoicesQueue, getVoice, renameVoice } from "../api.js";

const PLAY = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const EXPAND =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>';
const SHRINK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h4V4M20 8h-4V4M4 16h4v4M20 16h-4v4"/></svg>';
const PAUSE =
  '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
const DL =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>';
const BACK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>';
const SLIDES =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>';

// Сверху висят шапка сайта и липкий плеер: место под ними — слепая зона, и
// центр чтения надо считать от её низа, иначе активное слово уезжает под плеер.
// Высоту плеера меряем по факту, а не константой: на узком экране он ниже,
// да и любая правка стилей иначе разъехалась бы с этим числом молча.
//
// ⚠️ С шапкой ровно это и случилось: здесь стояла константа 56, а со знаком-рисунком шапка
// выросла до 71 — и кадр с пультом молча заезжали под неё на 15 пикселей. Теперь высоту ставит
// `main.js` в переменную стилей, и то же число видят CSS и этот файл.
const TOPBAR = () =>
  parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--topbar")) || 56;

// «Спросить про это место» временно снято: человек и так читает полную
// расшифровку, и просить агента пересказать её же — пустой ход. Механизм
// (взведённый контекст на один вопрос, окно реплик на сервере) цел и ждёт
// осмысленного применения — например, «где ещё об этом говорили».
const ASK_ENABLED = false;

let cache = null; // список записей грузим один раз на сессию
let current = null; // что открыто сейчас: чтобы не перерисовывать при смене секунды

async function findRecord(id) {
  cache ||= await getRecords();
  return cache.records.find((r) => r.id === id) || null;
}

/** Строка людей записи: «Выступали: … · Участвовали: … · голосов N, названы M». */
function peopleLine(meta) {
  const groups = [
    ["Выступали", meta.speakers || []],
    ["Участвовали", meta.participants || []],
  ].filter(([, names]) => names.length);
  const named = groups.reduce((n, [, names]) => n + names.length, 0);
  if (!groups.length && !meta.voices) return null;
  const parts = groups.map(([label, names]) =>
    el("span", { class: "rd-role" }, el("b", { text: `${label}: ` }), names.join(", ")));
  // Безымянных голосов не перечислить — но их число говорит, насколько списки полны.
  if (meta.voices > named) {
    parts.push(el("span", { class: "rd-role rd-voices",
      text: `голосов ${meta.voices}, ${named ? `названы ${named}` : "не назван никто"}` }));
  }
  return el("div", { class: "rd-people" }, ...parts.flatMap((p, i) => (i ? [el("span", { class: "dot" }), p] : [p])));
}


export async function renderReader(id, sec = 0, {
  onBack, onAsk, onShare, editing = false,
  // Правки на весь корпус — имя голоса и «починить везде» — отдельное право: без него карточка
  // голоса не открывается и вопрос «починить везде?» не задаётся, иначе человек получал бы
  // отказ сервера уже после того, как всё ввёл.
  fixes = false,
  // Вопрос к этой записи: набор кнопок из конфига корпуса и уход в общий разговор по снятой
  // галочке. Пусто — панели не будет вовсе (у пространства выключен поиск, например).
  presets = null, onAskCorpus = null, onOpenRecord = null,
} = {}) {
  const root = $("#reader");

  // Та же запись, другая секунда (пришли из другой цитаты) — не перестраиваем
  // страницу: перерисовка сбросила бы прокрутку и оборвала воспроизведение.
  if (current?.id === id) {
    current.goto(sec);
    return;
  }
  current?.dispose(); // ушли на ДРУГУЮ запись: снимаем подписки прежней

  root.replaceChildren(el("div", { class: "rec-sub", text: "Открываю запись…" }));
  const meta = await findRecord(id);
  if (!meta) {
    root.replaceChildren(el("div", { class: "rec-sub", text: "Запись не найдена" }));
    return;
  }
  // Страница записи — в цвете её ветки (владелец, 14.09): токены акцента ставятся на корень
  // читалки, и всё акцентное на ней — подпись раздела, кнопка пуска, полоса, «‹‹ ››», чипы —
  // становится цветом ветки. Корень один на все записи, поэтому у ветки без цвета токены
  // снимаются, а не остаются от прошлой записи.
  paintSection(root, meta.section || meta.group);
  // Видео может не быть вовсе — например, у перенесённого с прежнего сайта анонса.
  // Тогда страница остаётся текстовой: расшифровка есть, играть нечего.
  const src = mediaUrl(meta.media);
  const data = await getWords(id).catch(() => ({ aligned: false, turns: [] }));

  // --- шапка -------------------------------------------------------------
  const back = el("a", { class: "back", html: BACK }, "К записям");
  back.addEventListener("click", (event) => {
    event.preventDefault();
    onBack?.();
  });

  // Заголовок берём как есть: у подкаста из него регуляркой вынимались номер и темы,
  // у доклада заголовок — просто заголовок. В строчке над ним — митап, если он заполнен.
  const head = el(
    "div",
    { class: "rd-head" },
    meta.group ? el("span", { class: "num", text: meta.group }) : null,
    el("h2", {}, meta.award ? el("span", { class: "rec-award", text: "🏆", title: "Отмечено" }) : null, meta.title),
    el(
      "div",
      { class: "rd-meta" },
      fmtDate(meta.date),
      el("span", { class: "dot" }),
      fmtDuration(meta.duration_sec),
      (meta.kind || []).length ? el("span", { class: "dot" }) : null,
      (meta.kind || []).join(", "),
      // Категория ушла из этой строки в ряд чипов ниже (владелец, 14.09: «громоздко»): рядом с
      // форматом записи длинное название категории читалось как его повтор.
      // Ссылка на исходный пост: под ним обсуждение и вопросы, которых в расшифровке нет.
      meta.post ? el("span", { class: "dot" }) : null,
      meta.post
        ? el("a", { class: "rd-post", href: meta.post, target: "_blank", rel: "noopener", text: "исходный пост" })
        : null,
      meta.discussion ? el("span", { class: "dot" }) : null,
      meta.discussion
        ? el("a", { class: "rd-post", href: meta.discussion, target: "_blank", rel: "noopener", text: "обсуждение" })
        : null
    ),
    // Люди — по ролям, а не общим списком: кто выступал и кто спрашивал — разные вопросы.
    // Счётчик голосов говорит, скольких назвать не удалось.
    peopleLine(meta),
    // Категория — первым, акцентным чипом; темы — все, а не первые три, как на карточке: сюда
    // пришли читать.
    meta.category || (meta.topics || []).length
      ? el("div", { class: "rd-topics" },
          meta.category ? el("span", { class: "chip-topic chip-kind", text: meta.category }) : null,
          ...(meta.topics || []).map((t) => el("span", { class: "chip-topic", text: t })))
      : null,
    // Аннотация — целиком, без обрезки: сюда пришли читать, а не выбирать из списка. Где её нет —
    // краткое содержание по расшифровке (`blurb`), то же, что на карточке.
    meta.summary || meta.blurb ? el("p", { class: "rd-summary", text: meta.summary || meta.blurb }) : null
  );

  // --- плеер (липкий: до паузы не надо мотать страницу вверх) -------------
  const playBtn = el("button", { class: "pbtn", html: PLAY, "aria-label": "Слушать" });
  const bar = el("div", { class: "pbar" }, el("span", { class: "pfill" }));
  const fill = bar.querySelector(".pfill");
  const now = el("span", { text: "0:00" });
  const total = el("span", { text: fmt(meta.duration_sec) });
  const speed = el("button", {
    class: "pspeed",
    text: rateLabel(player.rate()),
    title: "Скорость воспроизведения",
    "aria-label": "Скорость воспроизведения",
  });
  // Перемотка по абзацам (владелец, 13.09): те же две кнопки, что и клавиши ← →. Логика в
  // records/seek.js: → — в начало следующего абзаца, ← — в начало текущего, а у самого начала —
  // на предыдущий, как на плеере с треками. Кнопки — две крошечные плоские, размером с сами
  // символы, НАД полосой слева (владелец, 14.09): на полосе будут именованные метки, и крупные
  // кнопки отнимали бы у неё место.
  const prevBtn = el("button", { class: "pjump", text: "‹‹", title: "Предыдущий абзац (←)", "aria-label": "Предыдущий абзац" });
  const nextBtn = el("button", { class: "pjump", text: "››", title: "Следующий абзац (→)", "aria-label": "Следующий абзац" });
  const jumps = el("span", { class: "pjumps" }, prevBtn, nextBtn);
  // Во весь экран. Прячется, пока не известно, есть ли в записи КАРТИНКА: у аудио разворачивать
  // нечего, а кнопка, открывающая чёрный прямоугольник, выглядит поломкой.
  const full = el("button", {
    class: "pspeed pfull",
    html: EXPAND,
    hidden: "",
    title: "Во весь экран",
    "aria-label": "Во весь экран",
  });
  // Субтитры. В полном экране включаются сами (там расшифровки не видно вовсе), а здесь —
  // выбор человека, и он запоминается между записями.
  const subs = el("button", {
    class: "pspeed psubs",
    text: "СУБ",
    hidden: "",
    title: "Субтитры поверх видео",
    "aria-label": "Субтитры",
  });
  const download = el("a", {
    class: "dl-btn",
    href: transcriptUrl(meta.id),
    html: DL,
    title: "Скачать расшифровку (.md)",
    download: "",
  });
  // Слайды — рядом с расшифровкой: доклад без них половинчатый, а лежат они
  // в том же каталоге записи, и сайт знает о них из шапки.
  const slides = meta.slides
    ? el("a", {
        class: "dl-btn",
        href: slidesUrl(meta.id),
        html: SLIDES,
        title: "Слайды доклада (PDF)",
        target: "_blank",
        rel: "noopener",
      })
    : null;

  // ⓘ Кнопки «поделиться» в пульте НЕТ (владелец, 14.09): ссылкой на запись делятся адресом
  // страницы, он и так в строке браузера. Меню «вся запись / с текущего места» объясняло, чем
  // именно делишься, но ради того же результата занимало место в пульте. Ссылка НА МОМЕНТ
  // осталась там, где она и нужна, — по клику на слово в расшифровке (`onShareAt` ниже).

  // Тумблер режима правки. Отдельный режим нужен потому, что клик по слову перематывает, и
  // отбирать это у читалки нельзя: перемотка по слову и есть её смысл. Показываем, только когда
  // правка включена в конфиге, — но это подсказка, а не рубеж: отказ даёт сервер.
  // Маленькая квадратная кнопка с карандашом и подсказкой (владелец, 14.09): слово «Править»
  // занимало место в пульте, а смысл кнопки и так читается по иконке.
  const PENCIL =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
  const editBtn = editing
    ? el("button", { class: "dl-btn rd-edit", html: PENCIL, title: "Править запись",
                     "aria-label": "Править запись" })
    : null;
  // В режиме правки карандаш сменяют две кнопки (владелец, 14.09: «не очевидно, где
  // Закончить»): зелёная «ОК» сохраняет и выходит, красная «Отменить» откатывает черновики
  // и выходит. Исход читается по цвету раньше, чем по слову.
  const okBtn = editing
    ? el("button", { class: "dl-btn rd-ok", text: "ОК", title: "Сохранить правки и выйти", hidden: "" })
    : null;
  const cancelBtn = editing
    ? el("button", { class: "dl-btn rd-cancel", text: "Отменить", title: "Выйти, ничего не сохраняя",
                     hidden: "" })
    : null;

  // ⚠️ **Играет — значит видно** (правило владельца 10.09). Кадр живёт в потоке страницы, а как
  // только доезжает до шапки — закрепляется под ней целиком. Промежуточного состояния «наполовину
  // под шапкой» нет вовсе: замерено на прежнем поведении — при прокрутке 400 от кадра оставалось
  // 97 пикселей из 344, а дальше он исчезал совсем, хотя запись играла.
  const screen = el("div", { class: "vscreen" });
  // Субтитры и полный экран — НА КАДРЕ, в правом нижнем углу (владелец, 14.09), видны при
  // наведении. Слой абсолютный: `place()` меряет высоту кадра, и оверлей её менять не должен.
  // Клик по кадру — это пауза (player.js), поэтому кнопки гасят всплытие (см. обработчики).
  screen.append(el("div", { class: "vover" }, subs, full));
  const controls = el(
    "div",
    { class: "player" },
    ...[
      // Пуск слева; над полосой — крошечные «‹‹ ››» перемотки по абзацам (владелец, 14.09).
      // Субтитры и полный экран уехали НА КАДР (`overlay`).
      src ? playBtn : null,
      el("div", { class: "ptrack" }, ...[src ? jumps : null, bar, el("div", { class: "ptime" }, now, total)].filter(Boolean)),
      src ? speed : null,
      slides,
      download,
      editBtn,
      okBtn,
      cancelBtn,
    ].filter(Boolean)
  );
  const dock = el("div", { class: "rd-dock" }, controls);

  const ask = ASK_ENABLED ? el("button", { class: "rd-ask", text: "Спросить про это место" }) : null;
  // Панель вопросов к ЭТОЙ записи — между пультом и расшифровкой (владелец, 14.09). Цитаты в
  // ответе — карточками-моментами, как в чате (владелец, 15.09: карточка с плеером и подсветкой
  // понятна, прыжок в расшифровку — нет). Второго `<video>` не появляется: адрес цитаты своей
  // записи равен `src`, и карточка играет ТЕМ ЖЕ элементом в кадре читалки. Появляется второй
  // ВЛАДЕЛЕЦ (`mN` против `rd:`), и всё, что ниже решалось по владельцу, разведено: липкость
  // кадра, кнопка пуска, полоса и субтитры — по АДРЕСУ («играет — значит видно», кто бы ни
  // включил); караоке и слежение за словом — только у владельца-читалки, иначе страница уезжала
  // бы от карточки, которую сейчас читают.
  const qa = presets
    ? createAskPanel({ record: meta, presets, onAskCorpus, onOpenRecord, onShareMoment: onShare })
    : null;
  const body = el("div", { class: "rd-transcript" });
  // Кнопка возврата: висит внизу и появляется, ТОЛЬКО когда звучащее место
  // уехало с экрана. Иначе она мозолила бы глаза всё время чтения.
  const backToLive = el("button", { class: "rd-live", hidden: "", text: "К звучащему месту" });
  // Пустые места отсеиваем ДО вставки: replaceChildren — нативный метод, он не
  // умеет пропускать null, а приводит его к строке и рисует текст «null».
  root.replaceChildren(
    ...[back, head, src ? screen : null, dock,
        ask && el("div", { class: "rd-tools" }, ask), qa?.node, body, backToLive].filter(Boolean)
  );
  // ⚠️ Дорожку субтитров ставим ТОЛЬКО когда общий элемент играет НАШУ запись: пока звучит
  // чужая, подменять её субтитры значит показать поверх чужого кадра наш текст.
  let subsArmed = false;
  const armSubs = () => {
    if (subsArmed || !src || !data.aligned || player.state().url !== src) return;
    // ⚠️ Флаг ВПЕРЁД вызова, а не после: `setSubtitles` рассылает состояние подписчикам, среди
    // них эта же читалка, и она снова зовёт `armSubs`. Со «сначала поставить, потом отметить»
    // получается бесконечная рекурсия — вкладка зависает намертво (поймано на живой странице).
    subsArmed = true;
    player.setSubtitles(vttUrl(data.turns));
  };

  if (src) {
    // Готовим запись СРАЗУ: пока человек читает, браузер забирает метаданные, и первый клик
    // по слову играет с нужного места, а не с начала. Ничего не качает целиком и не звучит.
    player.prime(src);
    // ⚠️ Кадр переносим сюда, только если элемент про ЭТУ запись. Пока звучит другая, показать
    // её кадр в рамке этой страницы значит соврать: человек решит, что играет то, что открыл.
    // Управление звучащим в это время живёт в панели шапки, а наша кнопка пуска перехватит.
    if (player.state().url === src) {
      player.attach(screen);
      // Запись уже звучит (пришли из панели в шапке) — дорожку ставим сразу, не дожидаясь
      // нажатия: субтитры нужны тому, кто УЖЕ смотрит.
      queueMicrotask(armSubs);
    }
  }

  // --- расшифровка -------------------------------------------------------
  // Название уезжает в общий плеер: с ним панель в шапке знает, что звучит и куда
  // вернуть человека, если он ушёл с читалки.
  const playOpts = { owner: `rd:${meta.id}`, title: meta.title, record: meta.id };
  // Где стояла страница, когда воспроизведение встало. Пока человек не тронул прокрутку,
  // всплывший кадр остаётся на месте; сдвинулся — кадр уезжает вместе с чтением.
  let якорь = null;
  const ОТПУСК = 24; // пикселей прокрутки, после которых кадр на паузе отпускаем

  /** Закреплён ли кадр под шапкой.
   *
   * ⚠️ Кадр НЕ ПЕРЕНОСИТСЯ между контейнерами — он просто становится липким на своём месте.
   * Перенос (кадр уезжал в отдельный блок внутри управления) стоил трёх механизмов сразу:
   * место в тексте схлопывалось, страницу приходилось подтягивать компенсацией, а на границе
   * условие дребезжало. Зарезервировать место вместо схлопывания тоже не вышло — при
   * прокрутке ВВЕРХ резерв виден пустым прямоугольником на пол-экрана. Липкость не меняет
   * поток вовсе: высота страницы та же, компенсировать нечего, дребезжать нечему.
   *
   * Кадр липнет, когда запись ЗАПУСКАЛИ и она играет (или только что встала на паузу).
   * Читающий, ничего не включавший, кадра под шапкой не увидит: он уедет вместе с текстом.
   */
  const place = () => {
    const s = player.state();
    let живой = false;
    if (src && s.url === src) {
      // Любой владелец, не только читалка: карточка-момент из панели (`mN`) играет эту же
      // запись тем же элементом. Без владельца — это `prime`: подготовлена, но не звучала.
      const запускали = s.owner != null;
      // ⚠️ На паузе кадр НЕ отклеиваем в тот же миг — «поставили и поставили паузу». Но и
      // держать намертво нельзя: он обязан уехать, как только человек снова листает. Помним,
      // где стояла страница в момент паузы, и отпускаем на первой же прокрутке.
      const стоим = якорь != null && Math.abs(scrollY - якорь) < ОТПУСК;
      живой = запускали && (s.playing || стоим);
    }
    // ⚠️ Дальше — и без видео тоже: пульт липнет у любой записи, а имя говорящего висит под
    // ним по `--kar-top`. Раньше без `src` выходили сразу, и имя висело по дефолту стилей —
    // под пультом; пока оно рисовалось НИЖЕ пульта, это не было видно.
    screen.classList.toggle("live", живой);
    // Пульт липнет ПОД кадром, а не на его место: два липких блока на одной высоте наложились
    // бы друг на друга. Высоту кадра отдаём стилям числом — в CSS её не вычислить.
    // ⚠️ ВПЛОТНУЮ, без зазора: в зазор просвечивал проезжающий текст, и между кадром и пультом
    // мелькала чужая строка. Отбивку даёт собственный отступ управления, он с фоном.
    const подКадром = живой ? TOPBAR() + screen.getBoundingClientRect().height : TOPBAR();
    dock.style.setProperty("--dock-top", `${Math.round(подКадром)}px`);
    // Липкое имя говорящего встаёт под управлением, а его высота меняется вместе с кадром.
    body.style.setProperty("--kar-top", `${Math.round(подКадром + dock.getBoundingClientRect().height)}px`);
    markStuck();
  };
  addEventListener("scroll", place, { passive: true });

  /** Разложить хвосты и вытеснение по именам говорящих — то, чего липкость сама не умеет.
   *
   * Под стопкой (пульт, а под ним повисшее имя) текст гаснет хвостом-градиентом. Хвост нельзя
   * держать у каждого имени всегда: в потоке он лёг бы на первую строку следующего абзаца.
   * Стили не знают, прилип ли элемент, поэтому здесь решается, у кого хвост горит (`--tail`):
   * у пульта — пока под ним никто не висит; у повисшего имени — целиком; у ПОДЪЕЗЖАЮЩЕГО —
   * разгорается на последних пикселях перед стыком. Без разгорания хвост включался бы
   * скачком, и строка под именем темнела бы рывком ровно в момент стыка; с ним разница в
   * покрытии на переключении — единицы процентов, замерено по формуле градиента.
   *
   * Имена липнут на одну высоту и НЕ выталкивают друг друга — следующее просто ложится
   * поверх, и над ним торчат верхушки букв прежнего. Поэтому вытеснение делаем сами:
   * прежнее имя уезжает вверх под пульт ровно на столько, на сколько следующее не дошло
   * до его места (`pushed`, там оно ниже пульта по z-index).
   *
   * Видимое из повисших — последнее по документу, поэтому обход обрывается на первом имени,
   * что ещё в потоке: оно и есть подъезжающее.
   */
  const РАЗГОН = 24; // за сколько пикселей до стыка хвост подъезжающего имени разгорается
  let помеченные = []; // кому ставили стили в прошлый раз — им же и сбрасываем
  const markStuck = () => {
    // ⚠️ Сброс ДО замеров: вытесненное имя сдвинуто вверх, и с этим сдвигом его низ равен
    // низу пульта — оно сошло бы за ушедшее с концом расшифровки, и стопка «пустела».
    for (const who of помеченные) {
      who.style.removeProperty("--tail");
      who.style.transform = "";
      who.classList.remove("pushed");
    }
    const dockBox = dock.getBoundingClientRect();
    const karTop = dockBox.bottom;
    // ⚠️ Хвост пульта горит, только когда пульт ПРИЛИП. В покое он стоит на своём месте в потоке,
    // под ним ничего не проезжает — а градиент висел всегда, и панель вопросов, стоящая сразу под
    // пультом, лежала в нём без всякой прокрутки: «кнопки затеняются, видно плохо» (владелец,
    // 15.09). Прилип — значит верх пульта упёрся в его липкий отступ (`--dock-top`, ставится в
    // `place` перед этим вызовом).
    const прилип = dockBox.top <= (parseFloat(dock.style.getPropertyValue("--dock-top")) || TOPBAR()) + 1;
    let висит = null;
    let едет = null;
    for (const who of body.querySelectorAll(".kar-who")) {
      const r = who.getBoundingClientRect();
      if (r.top > karTop + 1) { едет = who; break; }
      // Ушло вверх вместе с концом расшифровки — не висит, гасить под ним нечего.
      висит = r.bottom > karTop ? who : null;
    }
    помеченные = [висит, едет].filter(Boolean);
    dock.style.setProperty("--tail", висит || !прилип ? "0" : "1");
    // Низ стопки: под ним и висит хвост. Расстояние от него до подъезжающего имени решает и
    // вытеснение, и разгорание.
    const низ = висит ? висит.getBoundingClientRect().bottom : karTop;
    if (висит) висит.style.setProperty("--tail", "1");
    if (!едет) return;
    const зазор = едет.getBoundingClientRect().top - низ;
    if (висит && зазор < 0) {
      висит.style.transform = `translateY(${Math.round(зазор)}px)`;
      висит.classList.add("pushed");
    }
    const разгорание = Math.min(1, Math.max(0, (РАЗГОН - зазор) / РАЗГОН));
    if (разгорание > 0) едет.style.setProperty("--tail", разгорание.toFixed(3));
  };

  /** Подвинуть текст, ТОЛЬКО если всплывший кадр закрыл слово, по которому щёлкнули.
   *
   * Не перекрыто — не трогаем вовсе: лишнее движение текста мешает читать сильнее, чем помогает.
   * Перекрыто — двигаем ровно настолько, чтобы слово встало первой строкой под управлением.
   */
  const nudge = (at) => {
    const word = karaoke?.wordAt(at);
    if (!word) return;
    // Меряем ПОСЛЕ `place`: кадр уже всплыл, и высота липкого блока учитывает его.
    const край = dock.getBoundingClientRect().bottom + 6;
    const верх = word.node.getBoundingClientRect().top;
    if (верх >= край) return; // не перекрыто — не двигаем вовсе
    // ⚠️ БЕЗ `behavior: "smooth"`. Замерено 08.09 в браузере владельца: плавная прокрутка не
    // анимируется вовсе — `scrollBy({behavior:"smooth"})` не сдвигает страницу ни на пиксель,
    // тогда как мгновенная работает. Полагаться на неё значит не подвинуть текст совсем.
    scrollBy({ top: верх - край });
  };

  // Перехватываем звук — забираем и кадр: до этого он мог остаться на чужой странице.
  const seekTo = (at) => {
    player.play(src, { at, ...playOpts });
    armSubs();
    place();
    nudge(at);
  };

  let karaoke = null;
  let track = null;
  // Правки копятся здесь и уезжают ОДНОЙ пачкой по выходу из режима: иначе запись
  // пересобиралась бы после каждого слова. Ключ — номер абзаца, «было» держим ИСХОДНОЕ:
  // правил человек дважды или один раз, сервер видел только первый вариант.
  const drafts = new Map();
  place(); // высота липкого блока известна сразу — имя должно висеть под ним с начала
  if (data.aligned && data.turns.length) {
    karaoke = buildKaraoke(data.turns, {
      onSeek: seekTo,
      onShareAt: (at) => onShare?.(meta.id, at),
      onAsk: ASK_ENABLED ? (at) => onAsk?.(meta.id, Math.floor(at)) : undefined,
      onEdit: editing
        ? (i, was, now) => drafts.set(i, { turn: i, was: drafts.get(i)?.was ?? was, now })
        : undefined,
      // Карточка голоса — любому, кто правит: админу полная, остальным — «это я» у
      // безымянного голоса или справка у названного (право `claim`, см. app/auth/roles.py).
      onSpeaker: editing ? openSpeakerCard : undefined,
    });
    body.append(karaoke.node);
    place(); // имена уже в документе — открытая по ссылке на момент страница стоит не наверху
    // страница крутится целиком, поэтому scroller = null; сверху вычитаем липкое
    // Слепая зона сверху — до низа управления: под ним и кадр, и пульт, что бы из них ни липло.
    track = follower(null, { headroom: () => Math.max(TOPBAR(), dock.getBoundingClientRect().bottom) });
  } else {
    // Запись ещё не выровнена — честно говорим об этом, а не притворяемся,
    // будто караоке само по себе не работает.
    body.append(
      el("div", { class: "rec-sub", text: "Пословная расшифровка для этой записи ещё готовится." }),
      el("p", { class: "kar-text", text: "Скачайте расшифровку — она полная." })
    );
  }

  // --- правка голоса, не выходя из записи -----------------------------------
  //
  // ⚠️ Раньше метка говорящего уводила в верстак `/voices`: человек вылетал со страницы, терял
  // место и обрывал прослушивание, а чтобы поправить одну подпись, разглядывал список из 281
  // голоса. Теперь карточка открывается ПРЯМО В ТЕКСТЕ, показывает только этот голос и ничего
  // вокруг, а после сохранения метки переписываются на месте — звук не прерывается.
  //
  // ⚠️ Имя голоса — правка КОРПУСНАЯ: `Speaker_N` это идентификатор реестра, общий на все
  // записи. Поэтому карточка первым делом говорит, скольких записей это коснётся.
  let speakerCard = null;
  // Пока карточка открыта, слежение караоке за словом стоит (`track.lock`): иначе страница
  // уезжала к звучащему слову из-под поля ввода — «править спикера, пока идёт запись, нереально»
  // (владелец, 13.09). Звук при этом идёт: правя, хочется переслушать (правило 08.09).
  let unlockCard = null;
  const closeSpeakerCard = () => {
    speakerCard?.remove();
    speakerCard = null;
    unlockCard?.();
    unlockCard = null;
  };

  async function openSpeakerCard(id, shown, head) {
    closeSpeakerCard();
    if (!head) return;
    const card = el("div", { class: "sp-card" }, el("div", { class: "sp-wait", text: "Смотрю…" }));
    card.addEventListener("click", (event) => event.stopPropagation());
    speakerCard = card;
    unlockCard = track?.lock() ?? null;
    head.after(card);

    let voice;
    try {
      voice = await getVoice(id, meta.id);
    } catch (error) {
      card.replaceChildren(el("div", { class: "sp-wait", text: `Не открылось: ${error.message}` }));
      return;
    }
    if (speakerCard !== card) return; // пока ходили на сервер, открыли другую метку

    const status = el("div", { class: "sp-status" });
    const say = (text) => {
      status.textContent = text;
    };
    const close = el("button", { class: "sp-close", text: "Закрыть" });
    close.addEventListener("click", closeSpeakerCard);
    const мин = Math.round((voice.sec || 0) / 60);
    const охват = voice.known
      ? `${countOf(voice.records.length, "запись", "записи", "записей")} · ${мин} мин эфира`
      : "снимок голосов не собран — охват неизвестен";
    const head_ = el("div", { class: "sp-head" },
      el("span", { class: "sp-id", text: id }),
      el("span", { class: "sp-reach", text: охват }));
    // Самопредставление — сильнейшая подсказка: «меня зовут…» есть у 27 голосов из топ-30.
    // Показываем СЫРОЕ: финал-раунд умеет дочинить обрывок до правдоподобного имени. В карточке
    // «это я» оно же — способ убедиться, что голос правда твой, прежде чем назвать его везде.
    const intros = (voice.intros || []).slice(0, 2).map((i) => {
      const тут = i.record === meta.id;
      const кнопка = тут
        ? el("button", { class: "sp-at", text: fmt(i.sec), title: "Послушать это место" })
        : null;
      кнопка?.addEventListener("click", (event) => {
        event.stopPropagation();
        seekTo(i.sec);
      });
      return el("div", { class: "sp-intro" }, кнопка, el("span", { text: `«…${i.raw}…»` }));
    });
    const me = speakerName();

    // Не админ: имена голосов — правка на весь корпус, ему доступна только претензия «это я»
    // на безымянный голос. Названный — справка, чтобы молчание не читалось как поломка.
    if (!fixes) {
      if (voice.name) {
        card.replaceChildren(head_,
          el("div", { class: "sp-warn", text: `Голос назван: ${voice.name}. Изменить имя может администратор.` }),
          el("div", { class: "sp-row" }, close));
        return;
      }
      if (!can("claim") || !me) {
        card.replaceChildren(head_,
          el("div", { class: "sp-warn", text: "Назвать голос может администратор." }),
          el("div", { class: "sp-row" }, close));
        return;
      }
      const claim = el("button", { class: "sp-claim", text: `Это я — ${me}` });
      claim.addEventListener("click", async () => {
        claim.disabled = true;
        say("Записываю…");
        try {
          const out = await renameVoice(id, { claim: true });
          karaoke?.renameSpeaker(id, out.name);
          say(`Голос назван · пересобираю записей: ${out.queued.length}`);
          setTimeout(closeSpeakerCard, 1600);
        } catch (error) {
          claim.disabled = false;
          say(`Не сохранилось: ${error.message}`);
        }
      });
      card.replaceChildren(...[
        head_,
        el("div", { class: "sp-warn", text: "Имя применится ко ВСЕМ записям, где звучит этот голос — убедитесь, что это правда вы." }),
        ...intros,
        el("div", { class: "sp-row" }, claim, close),
        status,
      ].filter(Boolean));
      claim.focus();
      return;
    }

    const field = el("input", {
      class: "sp-name", type: "text", list: "sp-vocab", placeholder: "Имя и фамилия",
      value: voice.name || "",
    });
    // Словарь имён корпуса — подсказкой браузера: список короткий (сотня), а поиск по нему
    // человек и так ведёт глазами.
    const vocab = el("datalist", { id: "sp-vocab" },
      ...(voice.vocabulary || []).map((n) => el("option", { value: n })));

    const apply = async (name) => {
      say(name ? "Сохраняю…" : "Снимаю имя…");
      try {
        const out = await renameVoice(id, { name });
        karaoke?.renameSpeaker(id, name);
        // Метки уже переписаны на странице, поэтому перечитывать её не надо — и звук не рвётся.
        say(`${name ? "Имя применено" : "Имя снято"} · пересобираю записей: ${out.queued.length}`);
        setTimeout(closeSpeakerCard, 1600);
      } catch (error) {
        say(`Не сохранилось: ${error.message}`);
      }
    };

    // Кандидаты из меты; у безымянного голоса первым — сам вошедший («это я»): админ тоже
    // бывает докладчиком. Чип только подставляет имя, сохранение — обычное.
    const candidates = [
      ...(!voice.name && me ? [{ name: me, from: "это я", self: true }] : []),
      ...(voice.candidates || []).slice(0, 6),
    ];
    const chips = candidates.map((c) => {
      const chip = el("button", { class: c.self ? "sp-chip sp-chip-me" : "sp-chip",
                                  text: c.self ? `Это я — ${c.name}` : c.name,
                                  title: c.self ? "Подставить своё имя" : `из меты: ${c.from}` });
      chip.addEventListener("click", (event) => {
        event.stopPropagation();
        field.value = c.name;
        field.focus();
      });
      return chip;
    });

    const save = el("button", { class: "sp-save", text: "Сохранить" });
    const drop = el("button", { class: "sp-drop", text: "Убрать имя" });
    save.addEventListener("click", () => apply(field.value.trim()));
    drop.addEventListener("click", () => apply(""));
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") apply(field.value.trim());
      if (event.key === "Escape") closeSpeakerCard();
    });

    // ⚠️ Нативный replaceChildren пустых детей НЕ пропускает — null печатается строкой «null»
    // (ловилось 15.09 на сервере у голоса без кандидатов из меты), в отличие от нашего el().
    card.replaceChildren(...[
      head_,
      el("div", { class: "sp-warn", text: "Имя применится ко ВСЕМ записям, где звучит этот голос." }),
      ...intros,
      chips.length ? el("div", { class: "sp-chips" }, ...chips) : null,
      el("div", { class: "sp-row" }, field, vocab, save, drop, close),
      status,
    ].filter(Boolean));
    field.focus();
  }

  // --- режим правки -------------------------------------------------------
  //
  // Сохранение — ОДНО, по выходу из режима. Иначе запись пересобиралась бы после каждого слова,
  // а пересборка стоит секунд и трогает файлы, по mtime которых движок решает, что менять в базе.
  const note = el("div", { class: "rd-editnote", hidden: "" });
  dock.after(note);
  const say = (text) => {
    note.textContent = text;
    note.hidden = !text;
  };
  // ⚠️ Уход со страницы с несохранёнными правками — предупреждение. Без него работа теряется
  // молча, и человек об этом узнаёт, только перечитав запись.
  const guard = (event) => {
    if (!drafts.size) return;
    event.preventDefault();
    event.returnValue = "";
  };
  addEventListener("beforeunload", guard);

  /** «Починить везде»: правка становится правилом корпуса.
   *
   * Спрашиваем ПОСЛЕ правки, а не до: до неё непонятно, о чём вопрос. И спрашиваем только про
   * замену слова на слово — удаление и вставку правилом не выразить, у словаря нет такой формы.
   */
  async function offerEverywhere(pairs) {
    for (const { was, now } of pairs) {
      let reach = "";
      try {
        const found = await getTokens(was);
        if (!found.total) continue; // больше нигде не встречается — и спрашивать не о чем
        reach = `Так же написано ещё в ${found.records.length} записях (${found.total} раз).`;
        const others = found.variants.filter((v) => v.word !== was && v.word !== now);
        if (others.length) {
          reach += `\nРядом похожие написания: ${others.slice(0, 6).map((v) => v.word).join(", ")}.`;
        }
      } catch {
        continue; // не смогли посчитать охват — молчим, вслепую такое не предлагают
      }
      // Спрашиваем родным `confirm`: вопрос обязан ОСТАНОВИТЬ, а инструмент местный и
      // одноместный — своя модалка тут была бы обстановкой ради обстановки.
      if (!confirm(`Заменили «${was}» на «${now}».\n${reach}\n\nПочинить везде?`)) continue;
      try {
        const out = await promoteFix(was, now, meta.id);
        say(`Правило «${was}» → «${now}» заведено, записей затронуто: ${out.records.length}.`);
      } catch (error) {
        say(`Правило не завелось: ${error.message}`);
      }
    }
  }

  /** Дождаться, пока очередь пересборки опустеет, и перечитать страницу.
   *
   * Перечитываем целиком, а не подменяем текст: после сборки меняются и слова, и ВРЕМЕНА, и
   * границы абзацев. Показывать новый текст со старыми временами значило бы врать караоке.
   */
  async function waitForRebuild(tries = 40) {
    for (let i = 0; i < tries; i++) {
      await new Promise((done) => setTimeout(done, 700));
      try {
        const queue = await getVoicesQueue();
        if (!queue.pending && !queue.current) {
          location.reload();
          return;
        }
      } catch {
        return; // сервер не отвечает — перечитает человек сам, молча перезагружать не будем
      }
    }
    say("Пересборка идёт дольше обычного — перечитайте страницу позже.");
  }

  let editMode = false;

  // Карандаш виден вне режима; в режиме на его месте «ОК» и «Отменить».
  function showEditControls(on) {
    editBtn.hidden = on;
    okBtn.hidden = !on;
    cancelBtn.hidden = !on;
  }

  function enterEdit() {
    if (!editBtn) return;
    editMode = true;
    showEditControls(true);
    karaoke?.setEditing(true);
    say("Режим правки: у каждой реплики есть «Редактировать». «ОК» сохранит правки, «Отменить» вернёт всё как было.");
  }

  async function leaveEdit(save) {
    if (!editBtn) return;
    // «ОК» — жест сохранения: набранное в открытом поле применяем; «Отменить» — закрываем без.
    if (save) karaoke?.commitOpen();
    else karaoke?.discardOpen();
    karaoke?.setEditing(false);
    closeSpeakerCard();
    editMode = false;
    showEditControls(false);
    if (!save) {
      // Откат: абзацам возвращается исходный текст той же перестройкой, что и правка, —
      // «было» в черновике всегда исходное, даже если абзац правили дважды.
      for (const draft of drafts.values()) karaoke?.rewrite(draft.turn, draft.was);
      say(drafts.size ? "Правки отменены — ничего не сохранено." : "");
      drafts.clear();
      return;
    }
    if (!drafts.size) {
      say("");
      return;
    }
    const edits = [...drafts.values()];
    say("Сохраняю…");
    try {
      const out = await saveEdits(meta.id, edits);
      drafts.clear();
      say(`Сохранено правок: ${out.saved.length}. Пересобираю запись…`);
      if (fixes) await offerEverywhere(out.suggest || []);
      await waitForRebuild();
    } catch (error) {
      // Правки НЕ выбрасываем и из режима не выходим: причину можно починить и нажать «ОК»
      // ещё раз, а «Отменить» откатит всё честно. Раньше режим гас, а сообщение об ошибке
      // терялось под пультом — снаружи это выглядело как «не сохраняется» (владелец, 14.09).
      say(`Не сохранилось: ${error.message}`);
      editMode = true;
      showEditControls(true);
      karaoke?.setEditing(true);
    }
  }

  editBtn?.addEventListener("click", () => (editMode ? leaveEdit(true) : enterEdit()));
  okBtn?.addEventListener("click", () => leaveEdit(true));
  cancelBtn?.addEventListener("click", () => leaveEdit(false));

  // --- управление --------------------------------------------------------
  ask?.addEventListener("click", () => onAsk?.(meta.id, Math.floor(player.state().time || sec || 0)));
  playBtn.addEventListener("click", () => {
    armSubs();
    const s = player.state();
    if (s.url === src && s.playing) {
      player.pause(); // звучит эта запись — чей бы владелец ни был (карточка тоже): пауза
    } else if (s.url === src && s.owner) {
      // Уже включали (читалка или карточка) и стоит на паузе — продолжаем С ЭТОГО МЕСТА, владелец
      // теперь читалка. ⚠️ `toggle` при чужом владельце звал `play(..., {at: sec})`, а `sec` из
      // адреса у записи, открытой из списка, — ноль: пуск после карточки отматывал в начало.
      player.play(src, { at: null, ...playOpts });
    } else {
      player.toggle(src, { at: sec || 0, ...playOpts });
    }
    place();
  });
  // Перемотка по абзацам — тем же путём, что клик по слову и по полосе (`seekTo` + `reveal`):
  // `player.seek` без метаданных не работает, а `play(src, {at})` доигрывает перемотку сам.
  // Старты абзацев — те же, что у караоке (`turn.start`, мереные), их не двигает даже правка.
  const starts = (data.turns || []).map((t) => t.start ?? t.words?.[0]?.[1] ?? 0);
  // ⚠️ Пока видео не загрузилось (вне контура его нет вовсе, а из архива метаданные едут
  // секунды), время плеера стоит на нуле, и каждое → возвращало бы к первому абзацу. Поэтому,
  // пока плеер не отдаёт СВОЁ время этой записи, считаем от последней перемотки: стрелки ходят
  // по тексту и в режиме чтения, а звук догонит, когда приедет (`play` доигрывает `at` сам).
  let lastJump = null;
  const jump = (dir) => {
    const s = player.state();
    const live = s.url === src && s.time > 0 ? s.time : null;
    const at = live ?? lastJump ?? sec ?? 0;
    const to = dir < 0 ? prevTarget(starts, at) : nextTarget(starts, at);
    if (to === null) return;
    lastJump = to;
    seekTo(to);
    reveal(to);
  };
  prevBtn.addEventListener("click", () => jump(-1));
  nextBtn.addEventListener("click", () => jump(1));
  // ⚠️ Стрелки — только вне полей ввода и без модификаторов (`isSeekKey`): в режиме правки
  // человек ходит стрелками по тексту в textarea, а Cmd+← в браузере — «назад».
  const onArrows = (event) => {
    if (!src) return;
    // Пробел — пауза/пуск, ровно как кнопка «Слушать» (владелец, 13.09); без preventDefault
    // браузер ещё и прокрутил бы страницу на экран вниз.
    if (isPauseKey(event)) {
      event.preventDefault();
      playBtn.click();
      return;
    }
    if (!isSeekKey(event)) return;
    event.preventDefault();
    jump(event.key === "ArrowLeft" ? -1 : 1);
  };
  addEventListener("keydown", onArrows);
  // ⚠️ Разворачиваем КАДР вместе с его рамкой, а не сам `<video>`: только так поверх картинки
  // можно положить своё — субтитры, подсветку слова. У развёрнутого `<video>` браузер рисует
  // лишь кадр со своими кнопками, и наш слой туда не попадёт.
  // Кнопки лежат на кадре, а клик по кадру — пауза: гасим всплытие, иначе «во весь экран»
  // ещё и останавливал бы запись.
  full.addEventListener("click", (event) => { event.stopPropagation(); player.fullscreen(screen); });
  subs.addEventListener("click", (event) => { event.stopPropagation(); player.wantSubtitles(!player.subtitlesOn()); });
  speed.addEventListener("click", () => {
    speed.textContent = rateLabel(player.cycleRate());
  });
  bar.addEventListener("click", (event) => {
    const box = bar.getBoundingClientRect();
    const at = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * meta.duration_sec;
    seekTo(at);
    reveal(at); // перемотал полосой — текст обязан приехать следом
  });

  let revealTimers = null; // повторные наводки на место из ссылки — снимаем при уходе
  let wasPlaying = false;
  let wasBtnPlaying = null;
  let lastWidth = -1;
  let lastClock = "";
  let lastTotal = "";
  // Место, на которое просили встать ссылкой. Держим его, пока звук туда не
  // доедет: на свежей загрузке метаданных ещё нет, `currentTime` откладывается
  // до `loadedmetadata`, а заблокированный `play()` уже разослал состояние со
  // временем 0. Без этой защиты подсветка и прокрутка уезжают в начало записи
  // сразу после того, как мы навелись на нужную секунду.
  let pending = sec > 0 ? sec : null;
  const ARRIVED = 1.5; // с — попадание в запрошенное место

  const unsubscribe = player.subscribe((s) => {
    // Звучит ЭТА запись — по адресу: включить её могла и карточка-момент из панели. Кадр, кнопка
    // пуска, полоса и часы живут по адресу; караоке и слежение — только когда владелец читалка.
    const sounding = s.url === src;
    const mine = sounding && s.owner === `rd:${meta.id}`;
    if ((sounding && s.playing) !== wasBtnPlaying) {
      wasBtnPlaying = sounding && s.playing;
      // Встали — запоминаем место; заиграло — якорь больше не нужен.
      якорь = wasBtnPlaying ? null : scrollY;
      place(); // заиграло или встало — кадру, возможно, пора всплыть или вернуться
      playBtn.innerHTML = wasBtnPlaying ? PAUSE : PLAY;
    }
    playBtn.classList.toggle("playing", sounding && s.playing);
    speed.textContent = rateLabel(s.rate);
    full.hidden = !s.picture;
    full.innerHTML = s.full ? SHRINK : EXPAND;
    full.title = s.full ? "Свернуть" : "Во весь экран";
    // Субтитры прячем там же, где полный экран: у записи без картинки они бессмысленны —
    // весь текст и так лежит под плеером.
    subs.hidden = !s.picture || !s.hasSubs;
    subs.classList.toggle("on", s.subs);
    if (sounding) armSubs(); // дорожка — про запись, а не про того, кто её включил
    // ⚠️ Элемент один на приложение, и карточка ЧУЖОЙ записи в панели переключает его `src`:
    // оставь его в рамке — и в кадре этой записи заиграет чужое видео (правило 08.09: показать
    // здесь не своё — обман). Пока звучит другое, элемент паркуется, а рамка прячется; вернулась
    // своя запись — рамка возвращается на место. Без видео (`src` пуст) трогать нечего.
    if (src) {
      const here = screen.contains(player.element());
      if (!sounding && s.url && here) player.detach();
      else if (sounding && !here) player.attach(screen);
      screen.classList.toggle("parked", Boolean(!sounding && s.url));
    }
    if (!sounding) {
      karaoke?.clear();
      wasPlaying = false;
      return;
    }
    const length = s.duration || meta.duration_sec || 1;
    // Каждый кадр: полосу двигаем при заметном сдвиге, часы — при смене секунды.
    const width = Math.max(0, Math.min(1, s.time / length)) * 100;
    if (Math.abs(width - lastWidth) > 0.05) {
      fill.style.width = `${width}%`;
      lastWidth = width;
    }
    const clock = fmt(s.time);
    if (clock !== lastClock) {
      now.textContent = clock;
      lastClock = clock;
    }
    const whole = fmt(length);
    if (whole !== lastTotal) {
      total.textContent = whole;
      lastTotal = whole;
    }
    if (!mine) {
      // Играет карточка: свою подсветку она ведёт сама, а слежение читалки утащило бы страницу
      // от карточки, которую сейчас читают. Полоса и часы выше — про запись, они обновлены.
      karaoke?.clear();
      wasPlaying = false;
      return;
    }

    // Пока не доехали — время показываем, но подсветку и прокрутку не трогаем.
    // Предохранитель: если пошло воспроизведение и звук ушёл дальше пары секунд,
    // а мы всё ещё не там — перемотка не применилась (файл не догрузился), и
    // держать место дальше значит навсегда заморозить подсветку.
    if (pending != null && (Math.abs(s.time - pending) <= ARRIVED || (s.playing && s.time > 2))) {
      pending = null;
    }
    const word = pending == null ? karaoke?.at(s.time) || karaoke?.wordAt(s.time) : null;
    const started = s.playing && !wasPlaying;
    wasPlaying = s.playing;

    // Нажали «играть» — возвращаем внимание к звучащему месту. Это главный
    // способ вернуться, если человек ушёл читать в сторону: поставил паузу,
    // пустил снова — и текст опять перед глазами. Плавно, чтобы было видно,
    // куда именно тебя вернули.
    if (started && word) track?.jump(word, { smooth: !track.lost(word) });
    else if (word) track?.follow(word);

    backToLive.toggleAttribute("hidden", !(s.playing && word && track?.lost(word)));
  });

  backToLive.addEventListener("click", () => {
    // Именно слово, а не реплика: возвращаем ровно туда, где сейчас голос.
    const word = karaoke?.wordAt(player.state().time);
    if (word) track?.jump(word);
    backToLive.setAttribute("hidden", "");
  });

  /**
   * Показать место в тексте, а не только перемотать звук: человек, пришедший
   * по ссылке из цитаты, должен УВИДЕТЬ, куда попал. Прыгаем сразу, без
   * плавности — плавно ехать через весь часовой доклад незачем.
   */
  function reveal(at) {
    karaoke?.at(at); // обновляем подсветку под новое время
    const word = karaoke?.wordAt(at);
    if (word) track?.jump(word);
  }

  current = {
    id,
    goto(at) {
      if (at <= 0) return;
      pending = at > 0 ? at : null;
      seekTo(at);
      reveal(at);
    },
    /** Открыть панель вопросов под руку — зовётся кнопкой «Спросить» в шапке. */
    focusAsk() {
      if (!qa) return false;
      qa.focus();
      return true;
    },
    dispose() {
      qa?.dispose();
      unsubscribe();
      // ⚠️ Освобождаем blob-адрес дорожки: он живёт до перезагрузки страницы, а записей за
      // сессию открывают десятки.
      if (subsArmed) player.setSubtitles("");
      player.detach(); // элемент один на приложение: уносим его со страницы, звук не глушим
      track?.stop();
      karaoke?.dispose();
      removeEventListener("keydown", onArrows);
      removeEventListener("beforeunload", guard);
      removeEventListener("scroll", place);
      closeSpeakerCard();
      revealTimers?.timers.forEach(clearTimeout);
      for (const type of ["wheel", "touchmove", "keydown"]) {
        if (revealTimers) removeEventListener(type, revealTimers.mark);
      }
    },
  };

  if (sec > 0) {
    // Звук пробуем включить, но не настаиваем: переход по ссылке браузер не
    // считает жестом-разрешением, и play() там честно отказывает. Место в
    // тексте показываем в любом случае — оно и есть суть ссылки.
    seekTo(sec);
    reveal(sec);

    // Наводимся НЕ ОДИН раз. На первой загрузке вёрстка ещё едет: подъезжают
    // самохостящиеся шрифты и текст перекомпоновывается, а Safari вдобавок сам
    // восстанавливает прежнюю прокрутку уже ПОСЛЕ нашего перехода — место
    // уползает, и ссылка «на секунду» открывается не там.
    //
    // Признак «человек листает сам» берём из его собственных жестов, а не из
    // изменения scrollY: наша же прокрутка и восстановление браузером меняют
    // scrollY тоже, и проверка по нему отменяла повторную наводку зря — именно
    // на этом ссылки и не долетали в Safari.
    let touched = false;
    const mark = () => {
      touched = true;
    };
    for (const type of ["wheel", "touchmove", "keydown"]) {
      addEventListener(type, mark, { passive: true, once: true });
    }
    const settle = () => {
      if (!touched) reveal(sec);
    };
    requestAnimationFrame(settle);
    document.fonts?.ready.then(settle);
    const timers = [setTimeout(settle, 250), setTimeout(settle, 800)];
    revealTimers = { timers, mark };
  } else {
    scrollTo({ top: 0 });
  }
  if (!data.aligned) toast("Караоке для этой записи ещё готовится");
}

/** «1×», «1.5×» — короткая метка на кнопку-переключатель. */
function rateLabel(rate) {
  return `${String(rate).replace(".", ",")}×`;
}

/** Уходим с читалки — снимаем подписки. Звук НЕ глушим: это намеренно. */
export function leaveReader() {
  current?.dispose();
  current = null;
}

/** Поставить курсор в поле вопроса открытой записи. `false` — читалки нет или панель выключена,
 *  и тогда «Спросить» в шапке ведёт себя как раньше (уводит к общему вопросу). */
export function focusAsk() {
  return Boolean(current?.focusAsk?.());
}
