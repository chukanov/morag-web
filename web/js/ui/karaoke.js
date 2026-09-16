// Караоке: слово подсвечивается ровно когда звучит, клик по слову перематывает.
//
// Один модуль на весь сайт — им пользуются и читалка записи, и карточка-момент
// в ответе. Разница между ними только в том, сколько слов передали.
//
// Тайм-коды считает не браузер: их готовит `tools/align_words.py` (выравнивание
// текста по звуку) и отдаёт BFF. Здесь только отрисовка и поиск текущего слова.
import { el, fmt } from "./dom.js";
import { isKey, spanAt } from "../chat/gist.js";

const SHARE =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M12 15V3M8 7l4-4 4 4"/></svg>';

// Слово держим подсвеченным чуть дольше его конца: между словами есть зазоры
// в десятки миллисекунд, и без этого подсветка мигает на каждом стыке.
const HOLD = 0.25;

// Насколько звучащему слову позволено отойти от середины, прежде чем страницу
// подтянут. Слишком мало — прокрутка не успевает доехать и текст трясётся на
// каждом слове; слишком много — читаешь у самого края. Доля высоты чтения.
const DRIFT = 0.18;

/**
 * Строит расшифровку из слов и возвращает управление подсветкой.
 *
 * @param {object[]} turns реплики: {start, end, speaker, words: [[текст, старт, конец], …]}
 * @param {object}   opts  onSeek(sec) — куда перематывать по клику;
 *                         keywords — слова, которые движок счёл ключевыми (подсветка);
 *                         spans — важные места: они выделяются, остальное приглушается
 *                         (см. `chat/gist.js`);
 *                         inside — границы самой цитаты, когда вокруг взят контекст
 * @returns {{node: HTMLElement, at(time): void, clear(): void, seek(sec): void, words: number}}
 */
export function buildKaraoke(
  turns,
  { onSeek, onAsk, onShareAt, onEdit, onSpeaker, showTime = true, keywords = [], spans = [],
    inside = null } = {}
) {
  const node = el("div", { class: "kar" });
  // Горячие куски — отдельная сущность в тексте, а не «слова, которые не
  // приглушены»: когда мест несколько, надо видеть, что их несколько и где они.
  //
  // ⚠️ Прятать «воду» между ними мы пробовали и отказались (2026-08-16): текст
  // из обрывков с многоточиями выглядит странно и читается хуже целого, даже
  // когда самого текста в пять раз меньше. Показываем фрагмент целиком, важное
  // подсвечиваем, а взгляд наводим на первое выделенное место.
  const spanNodes = [];

  // Режим правки добавляет ровно одно: кнопку «Редактировать» у каждой реплики. Всё остальное
  // работает как обычно — и перемотка по слову, и подсветка.
  //
  // ⚠️ Сначала я их в этом режиме отключил, и это было неверно: правя реплику, человек первым
  // делом хочет её ПЕРЕСЛУШАТЬ (правка владельца 08.09). Отключение прикрывало настоящий
  // дефект — перерисовка правленого абзаца оставляла поиск звучащего слова с висящими узлами.
  // Лечится он в `reflow`, а не отобранной перемоткой.
  let editing = false;

  // Клик по слову перематывает — и тут же предлагает поделиться этим местом.
  // Так «дай ссылку на конкретную реплику» становится продолжением жеста,
  // которым человек и так пользуется, а не отдельной кнопкой где-то в плеере.
  //
  // Плашка позиционируется абсолютно, а не вставляется в текст: вставка сдвинула
  // бы строки, а под ними в этот момент бежит караоке — прыжок текста читать
  // невозможно.
  let shareChip = null;
  const hideShare = () => {
    shareChip?.remove();
    shareChip = null;
  };
  function offerShare(at, wordNode) {
    if (!onShareAt) return;
    hideShare();
    shareChip = el("button", {
      class: "kar-share",
      html: SHARE,
      title: `Скопировать ссылку на ${fmt(at)}`,
      "aria-label": `Скопировать ссылку на ${fmt(at)}`,
    });
    shareChip.addEventListener("click", (event) => {
      event.stopPropagation();
      onShareAt(at);
      hideShare();
    });
    node.append(shareChip);
    // Над словом и по его левому краю; если упирается в правый край — двигаем внутрь.
    const top = wordNode.offsetTop - shareChip.offsetHeight - 4;
    const maxLeft = node.clientWidth - shareChip.offsetWidth;
    shareChip.style.top = `${Math.max(0, top)}px`;
    shareChip.style.left = `${Math.max(0, Math.min(wordNode.offsetLeft, maxLeft))}px`;
  }
  // Клик мимо — плашка не нужна: человек уже занят другим. Слушатель вешаем,
  // только если делиться вообще предлагаем (в карточке-моменте своя кнопка),
  // и снимаем в dispose: читалка пересобирает караоке на каждую запись.
  if (onShareAt) document.addEventListener("click", hideShare);

  /** Клик по слову перематывает — и тут же предлагает поделиться этим местом.
   *
   * Отдельной функцией, потому что слова рождаются в ДВУХ местах: при первой отрисовке и при
   * перестройке правленого абзаца. Разойдись эти два места — и в правленой реплике перемотка
   * молча перестала бы работать, а заметили бы это через месяц.
   */
  function seekOnClick(word, timeOf) {
    word.addEventListener("click", (event) => {
      event.stopPropagation();
      onSeek?.(timeOf());
      offerShare(Math.floor(timeOf()), word);
    });
  }

  const flat = []; // все слова подряд — по ним ищем звучащее сейчас
  const lines = []; // реплики с их промежутками — по ним подсвечивается абзац
  const paragraphs = []; // абзацы для правки: узел и текст, который человек видит сейчас
  const speakerNodes = []; // метки говорящих: по ним имя обновляется на месте, без перезагрузки

  /** Перестроить правленый абзац, СОХРАНИВ слова, которых правка не коснулась.
   *
   * ⚠️ Не «перерисовать целиком», и это не косметика. По узлам слов идёт поиск звучащего
   * места: подмени их все — и перемотка по слову с подсветкой умерли бы ровно в том абзаце,
   * который человек только что правил. Совпадающие с краёв слова остаются СВОИМИ узлами и
   * своим временем; перестраивается только середина.
   *
   * Время середины считаем тем же правилом, что и сервер (`tools/turn_edits.py`): заменённые
   * делят промежуток тех, кого заменили, вставленные — паузу между соседями. Здесь это лишь
   * предпросмотр — точные времена приедут с пересборкой.
   */
  function reflow(i, next) {
    const para = paragraphs[i];
    const old = flat.slice(para.from, para.from + para.count);
    let head = 0;
    while (head < old.length && head < next.length && old[head].text === next[head]) head += 1;
    let tail = 0;
    while (tail < old.length - head && tail < next.length - head &&
           old[old.length - 1 - tail].text === next[next.length - 1 - tail]) tail += 1;
    const oldMid = old.slice(head, old.length - tail);
    const newMid = next.slice(head, next.length - tail);

    const lo = oldMid.length ? oldMid[0].start : old[head - 1]?.end ?? para.start;
    const hi = oldMid.length ? oldMid[oldMid.length - 1].end : old[head]?.start ?? para.end;
    const span = Math.max(0, hi - lo);
    const total = newMid.reduce((sum, w) => sum + w.length, 0) || newMid.length || 1;
    let cursor = lo;
    const fresh = newMid.map((text) => {
      const width = (span * text.length) / total;
      const entry = { start: cursor, end: cursor + width, text, node: null };
      const word = el("span", { class: "kar-w kar-fresh", text });
      seekOnClick(word, () => entry.start);
      entry.node = word;
      cursor += width;
      return entry;
    });

    const kept = [...old.slice(0, head), ...fresh, ...old.slice(old.length - tail)];
    para.body.replaceChildren();
    for (const entry of kept) para.body.append(entry.node, " ");
    flat.splice(para.from, para.count, ...kept);
    // Абзацы после этого сдвинулись в общем списке слов — иначе следующая правка перестроила
    // бы чужой абзац.
    const delta = kept.length - para.count;
    para.count = kept.length;
    if (delta) for (let k = i + 1; k < paragraphs.length; k += 1) paragraphs[k].from += delta;
    para.text = next.join(" ");
    // Состояние подсветки помнит НОМЕРА слов, а они только что поехали — начинаем заново.
    clear();
    passed = -1;
  }

  /** Открыть правку абзаца: поле с его текстом целиком.
   *
   * Целиком — потому что замена, удаление и вставка это три ИСХОДА одной правки, а не три
   * разные команды: их различает разница «было/стало», и отдельные кнопки на каждую были бы
   * сложностью, которой в данных нет.
   */
  function openEditor(i) {
    const para = paragraphs[i];
    if (!para || para.open) return;
    hideShare(); // плашка «поделиться» от прошлого клика по слову висела бы поверх поля
    const area = el("textarea", { class: "kar-area", rows: "4" });
    area.value = para.text;
    const ok = el("button", { class: "kar-ok", text: "Готово" });
    const no = el("button", { class: "kar-no", text: "Отмена" });
    const box = el("div", { class: "kar-editor" }, area,
                   el("div", { class: "kar-editrow" }, ok, no));
    box.addEventListener("click", (event) => event.stopPropagation());
    para.open = box;
    para.body.replaceWith(box);

    // Поле — ПО РАЗМЕРУ реплики. Замерено глазами на живой записи: реплика в 90 слов в поле
    // фиксированной высоты показывает четверть себя, да ещё и открывается прокрученной в конец
    // (курсор ставится в конец текста) — правишь, не видя начала. Растим по содержимому и
    // ставим курсор в НАЧАЛО.
    const fit = () => {
      area.style.height = "auto";
      area.style.height = `${area.scrollHeight}px`;
    };
    fit();
    area.addEventListener("input", fit);
    area.focus();
    area.setSelectionRange?.(0, 0);
    area.scrollTop = 0;

    const close = () => {
      box.replaceWith(para.body);
      para.open = null;
    };
    no.addEventListener("click", close);
    ok.addEventListener("click", () => {
      // Пробелы схлопываем: в пословных временах «слово» с пробелом молча отключает разрез
      // на абзацы, и часовой доклад становится одной простынёй с одним тайм-кодом.
      const text = area.value.replace(/\s+/g, " ").trim();
      close();
      if (!text || text === para.text) return;
      const was = para.text;
      reflow(i, text.split(" "));
      onEdit?.(i, was, text);
    });
  }

  let prevSpeaker = null;
  for (const [index, turn] of turns.entries()) {
    const line = el("div", { class: "kar-turn" });
    let head = null;
    if (turn.speaker) {
      // Имя пишем только при СМЕНЕ говорящего. Длинный доклад приезжает абзацами (каждый со
      // своим измеренным тайм-кодом — см. tools/make_record.py), и одно и то же имя шестьдесят
      // раз подряд читалось бы как перебивка, а не как один человек. Кнопка времени остаётся у
      // КАЖДОГО абзаца: это и есть «слушать отсюда», ради неё абзацы и появились.
      const same = turn.speaker === prevSpeaker;
      // ⚠️ Шапка — ТОЛЬКО при смене говорящего. У продолжения речи её больше нет вовсе: пустая
      // строка над каждым абзацем и была тем отступом, который резал глаз (правка владельца).
      head = same ? null : el("div", { class: "kar-who" });
      // Метка говорящего — вход в верстак имён: правку делаешь там, где заметил, а правило
      // остаётся одно. Сырую метку (`Speaker_N`) подсвечиваем: её видно как «не назван».
      if (!same) {
        const raw = /^Speaker_\d+$/.test(turn.speaker);
        const id = turn.speaker_id || turn.speaker;
        const name = el("button", {
          class: raw ? "kar-name raw" : "kar-name",
          text: turn.speaker,
          title: onSpeaker ? (raw ? "Назвать этот голос («это я») — в режиме правки" : "Голос назван — в режиме правки") : "",
        });
        // ⚠️ Вне режима правки метка — просто подпись, и клик по ней НИЧЕГО не делает.
        // Раньше она уводила в верстак имён: человек, читая запись, случайно вылетал со
        // страницы (и терял место), а правка спикеров ничем не отличалась от прослушивания.
        // Правка голоса — часть правки записи, поэтому живёт в её режиме (решение владельца).
        name.addEventListener("click", (event) => {
          event.stopPropagation();
          if (!editing) return;
          onSpeaker?.(id, turn.speaker, head);
        });
        speakerNodes.push({ id, node: name });
        head.append(name);
      }
      prevSpeaker = turn.speaker;
      // «Спросить про это» у самой реплики, а не только у плеера: человек
      // читает и цепляется за конкретное место — вопрос должен рождаться там,
      // где взгляд, а не там, где сейчас стоит звук.
      if (onAsk && head) {
        const ask = el("button", { class: "kar-ask", text: "спросить", title: "Спросить про эту реплику" });
        ask.addEventListener("click", (event) => {
          event.stopPropagation();
          onAsk(turn.start);
        });
        head.append(ask);
      }
      // ⚠️ Шапка — СОСЕД абзацев, а не ребёнок одного из них. Липкий элемент прилипает внутри
      // своего родителя: лежи имя внутри абзаца, оно висело бы ровно до конца этого абзаца, а
      // нужно — пока идут реплики этого человека. Соседи же выталкивают друг друга сами.
      if (head) node.append(head);
    }

    // Время — СЛЕВА от первой строки, в своей колонке. Сверху оно требовало отдельной строки
    // на каждый абзац, и текст рвался на куски широкими промежутками.
    if (showTime) {
      const tc = el("button", { class: "kar-tc", text: fmt(turn.start), title: "Слушать отсюда" });
      tc.addEventListener("click", (event) => {
        event.stopPropagation();
        onSeek?.(turn.start);
      });
      line.append(tc);
    }

    const body = el("p", { class: "kar-text" });
    let box = null; // обёртка текущего горячего куска
    let boxSpan = -1; // какому куску она принадлежит (кусок бывает и на две реплики)
    for (const [text, start, end] of turn.words) {
      const at = spanAt(start, spans);
      // Каждый кусок — свой элемент, поэтому его видно целиком и можно
      // подсветить, когда он звучит.
      if (at < 0) box = null;
      else if (!box || boxSpan !== at) {
        // Кусок, перешагнувший через реплику, получает по обёртке в каждой:
        // один элемент нельзя разложить в два абзаца.
        box = el("span", { class: "kar-hot" });
        boxSpan = at;
        (spanNodes[at] ||= []).push(box);
        body.append(box);
      }
      const word = el("span", { class: "kar-w", text });
      if (isKey(text, keywords)) word.classList.add("kar-key");
      // Подтянули разговор вокруг — видно, где кончается сама цитата: иначе
      // «шире» превращает точную ссылку в неотличимую простыню.
      if (inside && (start < inside.start || start > inside.end)) word.classList.add("kar-out");
      // ⚠️ Перемотка по слову работает И В РЕЖИМЕ ПРАВКИ. Отключать её нельзя: правя реплику,
      // человек в первую очередь хочет её ПЕРЕСЛУШАТЬ, и отобранная перемотка мешает ровно
      // там, где она нужнее всего (правка владельца 08.09).
      seekOnClick(word, () => start);
      (box || body).append(word, " ");
      flat.push({ start, end, node: word, text });
    }
    // Кнопка правки — эфемерная и справа: вне режима её нет вовсе, в режиме она появляется у
    // каждой реплики. Вешаем ПОСЛЕ тела: ей нужен готовый абзац.
    const words = turn.words || [];
    paragraphs.push({
      body,
      text: words.map((w) => w[0]).join(" "),
      open: null,
      // Границы абзаца в общем списке слов: по ним перестраивается правленый абзац.
      from: flat.length - words.length,
      count: words.length,
      start: turn.start ?? words[0]?.[1] ?? 0,
      end: turn.end ?? words.at(-1)?.[2] ?? 0,
    });
    if (onEdit) {
      const edit = el("button", { class: "kar-edit", text: "Редактировать",
                                  title: "Править эту реплику целиком" });
      edit.addEventListener("click", (event) => {
        event.stopPropagation();
        openEditor(index);
      });
      // Кнопка ложится ПОВЕРХ угла абзаца и места в потоке не занимает: у продолжения речи
      // шапки больше нет, а править нужно каждый абзац.
      line.append(edit);
    }
    line.append(body);
    node.append(line);
    // Промежуток реплики берём из данных, а если их нет — по её же словам.
    // Именно по нему подсвечивается абзац: между словами речь не прерывается,
    // и вести подсветку от текущего СЛОВА значит гасить её в каждой паузе.
    lines.push({ start: paragraphs[index].start, end: paragraphs[index].end, node: line });
  }

  // Поиск текущего слова — двоичный: их тысячи на запись, а звать
  // это приходится каждый кадр. Работает только на упорядоченном списке —
  // порядок гарантирует инструмент выравнивания.
  function indexAt(time) {
    let lo = 0;
    let hi = flat.length - 1;
    let hit = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (flat[mid].start <= time) {
        hit = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return hit;
  }

  let lit = null;
  let litLine = null;
  let passed = -1;

  /** Реплика, звучащая на этой секунде (по её промежутку, а не по словам). */
  function lineAt(time) {
    let lo = 0;
    let hi = lines.length - 1;
    let hit = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (lines[mid].start <= time) {
        hit = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    const line = hit >= 0 ? lines[hit] : null;
    return line && time <= line.end + HOLD ? line : null;
  }

  function at(time) {
    // Абзац держим подсвеченным, пока идёт реплика, — он не обязан мигать
    // вместе со словами. Отдельно от слова ещё и потому, что слово гаснет
    // в паузах, а спикер в это время никуда не делся.
    const line = lineAt(time);
    if (line !== litLine) {
      litLine?.node.classList.remove("live");
      line?.node.classList.add("live");
      litLine = line;
    }

    const i = indexAt(time);
    const current = i >= 0 && time <= flat[i].end + HOLD ? flat[i] : null;
    if (current === lit) return null;

    lit?.node.classList.remove("on");
    current?.node.classList.add("on");
    lit = current;

    // «Прочитанное» отмечаем пачкой — от прежнего места до нынешнего, а не
    // перебирая весь текст каждый кадр. Границы разные в две стороны: вперёд
    // гасим всё ДО текущего слова (само оно ещё звучит), назад — включая то,
    // на котором стояли, иначе после отката оно осталось бы серым.
    if (i !== passed) {
      if (i > passed) {
        for (let k = Math.max(0, passed); k < i; k++) flat[k].node.classList.add("done");
      } else {
        for (let k = Math.max(0, i); k <= passed && k < flat.length; k++) {
          flat[k].node.classList.remove("done");
        }
      }
      passed = i;
    }
    return current;
  }

  function clear() {
    lit?.node.classList.remove("on");
    litLine?.node.classList.remove("live");
    lit = null;
    litLine = null;
  }

  /**
   * Слово, к которому надо вести взгляд на этой секунде — даже если ровно
   * сейчас не звучит ни одно.
   *
   * Отличается от подсвеченного: в паузе между словами подсветки нет, и
   * наводиться было бы не на что — прыжок уходил к началу абзаца. Берём первое
   * ещё не отзвучавшее слово: речь идёт вперёд, туда и смотрим.
   */
  function wordAt(time) {
    if (!flat.length) return null;
    const i = indexAt(time);
    if (i >= 0 && time <= flat[i].end + HOLD) return flat[i]; // внутри слова
    const next = i + 1;
    return next < flat.length ? flat[next] : flat[flat.length - 1];
  }

  return {
    node,
    at,
    clear,
    dispose() {
      hideShare();
      if (onShareAt) document.removeEventListener("click", hideShare);
    },
    /** Включить или выключить режим правки. */
    setEditing(on) {
      editing = !!on;
      node.classList.toggle("kar-editing", editing);
    },
    /** Переписать метки одного голоса ПРЯМО НА СТРАНИЦЕ.
     *
     * Ради того, чтобы не перезагружать читалку после правки имени: перезагрузка оборвала бы
     * воспроизведение, а человек правит метку, слушая запись, и возвращаться ему некуда.
     */
    renameSpeaker(id, name) {
      for (const met of speakerNodes) {
        if (met.id !== id) continue;
        met.node.textContent = name || id;
        met.node.classList.toggle("raw", !name);
      }
    },
    /** Закрыть открытое поле, ПРИМЕНИВ написанное. Выход из режима — жест сохранения, и
     * потерять на нём набранный текст было бы худшим из возможных ответов. */
    commitOpen() {
      for (const para of paragraphs) para.open?.querySelector(".kar-ok")?.click();
    },
    /** Закрыть открытое поле, НЕ применяя написанное — «Отменить» на пульте. */
    discardOpen() {
      for (const para of paragraphs) para.open?.querySelector(".kar-no")?.click();
    },
    /** Вернуть абзацу текст (откат черновика по «Отменить»): та же перестройка, что у правки,
     * поэтому нетронутые слова остаются своими узлами, а караоке не теряет место. */
    rewrite(i, text) {
      const para = paragraphs[i];
      if (para && !para.open && para.text !== text) reflow(i, text.split(" "));
    },
    wordAt,
    words: flat.length,
    first: flat[0]?.start ?? 0,
    /** Сколько горячих кусков нашлось — их номера и подсвечиваются при игре. */
    hotCount: spanNodes.filter(Boolean).length,
    /** Первый выделенный кусок — к нему ведут взгляд, когда карточка открылась. */
    firstHot: spanNodes.find(Boolean)?.[0] ?? null,
    /**
     * Отметить кусок, который звучит сейчас. Слушая выжимку, человек прыгает
     * между местами разговора — без отметки непонятно, какое из них играет.
     */
    setActiveSpan(index) {
      spanNodes.forEach((boxes, i) => {
        for (const box of boxes || []) box.classList.toggle("on-air", i === index);
      });
    },
    // `at()` отдаёт слово только при СМЕНЕ — иначе бы мы каждый кадр трогали
    // DOM зря. Но чтобы навестись на звучащее место по команде (нажали
    // «играть», перемотали), нужно уметь спросить текущее слово отдельно.
    get current() {
      return lit;
    },
  };
}

/**
 * Прокрутка к активному слову — без рывков и не перебивая человека.
 *
 * `scroller` — элемент с собственной прокруткой либо `null`, если крутится вся
 * страница (так у читалки: высоту вью никто не ограничивает, и внутренний блок
 * попросту не скроллится — на этом слежение сначала и не работало).
 *
 * `headroom` — сколько сверху занято липкими шапкой и плеером: без этого
 * активное слово регулярно уезжает ПОД них и «подсвечено» невидимое место.
 * Можно передать функцию — тогда высота берётся из живой вёрстки и не
 * расходится с ней при правке стилей или на узком экране.
 */
export function follower(scroller, { pauseMs = 2500, headroom = 0 } = {}) {
  const blocked = () => (typeof headroom === "function" ? headroom() : headroom);
  // globalThis, а не window: в браузере это то же окно, но модуль остаётся
  // проверяемым вне его — а караоке слишком легко сломать незаметно
  const target = scroller || globalThis;
  let held = false;
  let timer = null;
  // ⚠️ Замок «человек правит» (владелец, 13.09): пока открыта карточка голоса или поле правки,
  // слежение за словом стоит, а звук идёт. Без замка `follow` подтягивал страницу к звучащему
  // слову, и карточка с полем ввода уезжала из-под рук — «править спикера, пока идёт запись,
  // нереально». Пауза записи была бы хуже: правя, хочется ПЕРЕСЛУШАТЬ (правило 08.09).
  // Замок счётный (карточка и поле могут быть открыты разом) и дублируется проверкой фокуса:
  // поле правки абзаца открывает караоке само, ему замок не выдают — хватает фокуса в textarea.
  let locks = 0;
  const typing = () => {
    const doc = globalThis.document;
    const el = doc && doc.activeElement;
    const tag = el && el.tagName ? el.tagName.toLowerCase() : "";
    return tag === "input" || tag === "textarea" || Boolean(el && el.isContentEditable);
  };
  const onScroll = () => {
    held = true;
    clearTimeout(timer);
    timer = setTimeout(() => (held = false), pauseMs);
  };
  target.addEventListener("scroll", onScroll, { passive: true });

  /** Видимое окно чтения: у элемента — он сам, у страницы — вьюпорт минус липкое. */
  function viewport() {
    const gap = blocked();
    if (scroller) {
      const box = scroller.getBoundingClientRect();
      return { top: box.top + gap, height: box.height - gap };
    }
    return { top: gap, height: innerHeight - gap };
  }

  function shift(distance, smooth) {
    const how = { top: distance, behavior: smooth ? "smooth" : "auto" };
    if (scroller) scroller.scrollBy(how);
    else scrollBy(how);
  }

  return {
    /**
     * Показать слово немедленно и без плавности — переход по ссылке на момент
     * и возврат внимания к звучащему месту (например, после паузы).
     */
    jump(word, { smooth = false } = {}) {
      if (!word) return;
      const view = viewport();
      const spot = word.node.getBoundingClientRect();
      shift(spot.top - view.top - view.height / 2 + spot.height / 2, smooth);
      held = false; // это наш собственный скролл, а не человеческий
      clearTimeout(timer);
    },
    follow(word) {
      if (!word || held || locks > 0 || typing()) return;
      const view = viewport();
      const spot = word.node.getBoundingClientRect();
      const middle = spot.top - view.top - view.height / 2 + spot.height / 2;
      // дёргаем прокрутку, только когда слово ушло из середины: иначе плавная
      // прокрутка не успевает доехать и страница трясётся на каждом слове
      if (Math.abs(middle) > view.height * DRIFT) shift(middle, true);
    },
    /** Ушло ли звучащее слово из поля зрения — по этому решаем, звать ли назад. */
    lost(word) {
      if (!word) return false;
      const view = viewport();
      const spot = word.node.getBoundingClientRect();
      return spot.bottom < view.top || spot.top > view.top + view.height;
    },
    /** Человек прокрутил сам — слежение отступило? */
    get held() {
      return held;
    },
    /** Замок на время правки: вернуть функцию, снимающую его. Снимать ровно один раз. */
    lock() {
      locks += 1;
      let done = false;
      return () => {
        if (done) return;
        done = true;
        locks = Math.max(0, locks - 1);
      };
    },
    get locked() {
      return locks > 0 || typing();
    },
    stop() {
      target.removeEventListener("scroll", onScroll);
      clearTimeout(timer);
    },
  };
}
