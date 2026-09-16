// Ход диалога: сессия, история, разбор кадров потока.
//
// Приватность: диалоги живут только в браузере гостя. На сервере чужие разговоры
// никому не показываются — это осознанная замена OWUI с общим аккаунтом.
import { $, toast } from "../ui/dom.js";
import { ask } from "../api.js";
import { createTurn } from "./view.js";
import { newSessionId, saveDialog, loadDialog } from "./history.js";
import { showTopic, clearTopic, hasTopic, reserveTopic, dropReservation } from "./topic.js";

const PLACEHOLDER_FIRST = "Спросите про доклад, тему или спикера…";
const PLACEHOLDER_NEXT = "Спросите ещё или уточните ответ…";

export function createChat({ onOpenRecord, onShareMoment, onSend, onSaved }) {
  const stream = $("#stream");
  const form = $("#form");
  const input = $("#q");
  const sendBtn = $("#send");

  let sessionId = newSessionId();
  let history = [];
  let turns = []; // {question, answer, citations} — из них собираются список и .md
  let topic = null;
  let busy = false;
  let controller = null;
  // ⚠️ Номер прогона. `reset()`/`adopt()` обрывают текущий ответ и тут же может уйти новый —
  // с главной так и происходит: сброс и отправка в ОДНОМ тике. `finally` прежнего `send`
  // срабатывает на следующей микрозадаче и без этой проверки снимал бы резерв темы уже НОВОГО
  // диалога (заставка гасла, тема не показывалась), а заодно обнулял `busy` — кнопка «стоп»
  // переставала работать. Прогон, который перестал быть текущим, за собой не убирает.
  let run = 0;

  updateComposer();

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (busy) return stop(); // во время ответа та же кнопка — «остановить»
    const question = input.value.trim();
    if (!question) return;
    input.value = ""; // вопрос ушёл в ленту; при остановке вернём его обратно
    const context = armed;
    disarm();
    send(question, context);
  });

  // Контекст «спросили из читалки» ждёт ОДИН следующий вопрос. Держим его
  // здесь, а не в читалке: человек мог уйти с неё, передумать и спросить
  // что-то своё — тогда прежнее место молча приклеилось бы к чужому вопросу.
  let armed = null;
  const basePlaceholder = input.placeholder;

  function disarm() {
    armed = null;
    input.placeholder = basePlaceholder;
    document.body.removeAttribute("data-armed");
  }

  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && armed) disarm();
  });

  /** Остановка: ответ обрывается, вопрос возвращается в строку для правки. */
  function stop() {
    if (!controller) return;
    const question = controller.question;
    controller.abort();
    controller = null;
    busy = false;
    updateComposer();
    input.value = question;
    input.focus();
    input.setSelectionRange(question.length, question.length);
    toast("Ответ остановлен");
  }

  async function send(question, context = null) {
    if (busy) return;
    busy = true;
    const myRun = ++run;
    onSend?.();
    updateComposer();

    // место под тему занимаем СРАЗУ: когда она придёт, вёрстка уже не сдвинется
    const expectsTopic = !hasTopic();
    if (expectsTopic) reserveTopic(stream);

    const turn = createTurn(question, { context, onOpenRecord, onShareMoment, onFeedback: sendFeedback });
    stream.append(turn.node);
    turn.node.scrollIntoView({ behavior: "smooth", block: "end" });

    const abort = new AbortController();
    abort.question = question; // чтобы вернуть текст в строку при остановке
    controller = abort;

    const answerParts = [];
    const citations = [];
    const records = new Set();
    let failed = false;
    let finished = false;
    let gotTopic = false;

    try {
      for await (const frame of ask(
        {
          question,
          sessionId,
          history,
          context: context ? { record_id: context.record_id, sec: context.sec } : null,
          wantTopic: expectsTopic, // решено ДО резерва: сам резерв темой не считается
        },
        abort.signal
      )) {
        switch (frame.type) {
          case "answer_id":
            turn.setAnswerId(frame.id);
            break;
          case "status":
            turn.addStatus(frame.text);
            break;
          case "token":
            answerParts.push(frame.text);
            turn.addToken(frame.text);
            break;
          case "citation":
            citations.push(frame);
            turn.addCitation(frame);
            if (frame.rec) records.add(frame.rec);
            break;
          case "topic":
            // приходит рано, ещё во время поиска: тема считается по вопросу
            topic = { title: frame.title, summary: frame.summary };
            gotTopic = true;
            showTopic(stream, { ...frame, records: [...records] }, { onOpenRecord });
            break;
          case "done":
            finished = true;
            busy = false;
            turn.finish();
            updateComposer();
            break;
          case "error":
            failed = true;
            turn.fail(frame.message || "Что-то пошло не так.");
            break;
          default:
            break; // незнакомый кадр — не ломаемся
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        turn.node.remove(); // остановленный ход не оставляем огрызком
      } else {
        failed = true;
        turn.fail(
          turn.hasText()
            ? "Связь оборвалась — ответ показан не полностью."
            : "Связь с сервером прервалась. Попробуйте ещё раз."
        );
      }
    } finally {
      // Прогон обошли сбросом или другим диалогом — состояние уже не наше, не трогаем.
      // ⓘ `stop()` номер не меняет: остановленный ответ обязан снять резерв темы сам.
      if (run === myRun) {
        if (expectsTopic && !gotTopic) dropReservation(); // тема не пришла — место не держим
        if (!failed && !finished) turn.finish();
        busy = false;
        controller = null;
        updateComposer();
        if (!failed && finished) {
          history.push({ role: "user", content: question });
          history.push({ role: "assistant", content: answerParts.join("") });
          turns.push({ question, answer: answerParts.join(""), citations });
          saveDialog({ id: sessionId, title: topic?.title, turns, topic });
          onSaved?.();
        }
      }
    }
  }

  function updateComposer() {
    input.placeholder = turns.length ? PLACEHOLDER_NEXT : PLACEHOLDER_FIRST;
    sendBtn.classList.toggle("stop", busy);
    sendBtn.setAttribute("aria-label", busy ? "Остановить" : "Отправить");
    sendBtn.title = busy ? "Остановить ответ" : "Отправить";
  }

  async function sendFeedback(answerId, vote) {
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

  function clearStream() {
    stream.querySelectorAll(".turn").forEach((node) => node.remove());
    clearTopic();
  }

  /** Перерисовать готовый диалог (свой из истории или чужой опубликованный). */
  function adopt(saved) {
    run++;
    controller?.abort();
    controller = null;
    busy = false;
    clearStream();

    sessionId = saved.id || newSessionId();
    topic = saved.topic || null;
    turns = saved.turns.map((t) => ({ ...t }));
    history = turns.flatMap((t) => [
      { role: "user", content: t.question },
      { role: "assistant", content: t.answer },
    ]);

    if (topic?.title) {
      const eps = [...new Set(turns.flatMap((t) => t.citations.map((c) => c.rec)).filter(Boolean))];
      showTopic(stream, { ...topic, records: eps }, { onOpenRecord });
    }
    for (const t of turns) {
      const turn = createTurn(t.question, { onOpenRecord, onShareMoment, onFeedback: sendFeedback, restored: true });
      stream.append(turn.node);
      turn.restore(t);
    }
    updateComposer();
    stream.lastElementChild?.scrollIntoView({ block: "end" });
    return true;
  }

  return {
    send,
    askFromLine: (question, context) => send(question, context),

    /** Взвести контекст места: следующий вопрос уйдёт вместе с ним (Esc — отменить). */
    armContext(context, hint = "Что спросить про это место?") {
      armed = context;
      input.placeholder = hint;
      document.body.setAttribute("data-armed", "1");
      input.focus({ preventScroll: true });
    },
    stop,
    /** Идентификатор текущего разговора — он же адрес страницы диалога. */
    get sessionId() {
      return sessionId;
    },
    get turns() {
      return turns;
    },
    get topic() {
      return topic;
    },

    /** «Новый чат»: прошлый диалог остаётся в списке, начинаем чистый лист. */
    reset() {
      run++;
      controller?.abort();
      busy = false;
      controller = null;
      sessionId = newSessionId();
      history = [];
      turns = [];
      topic = null;
      clearStream();
      disarm(); // взведённое из читалки место не должно приклеиться к вопросу с главной
      updateComposer();
    },

    /** Открыть сохранённый диалог: ходы перерисовываются, нить продолжается. */
    open(id) {
      const saved = loadDialog(id);
      return saved ? adopt(saved) : false;
    },

    /** Продолжить чужой (опубликованный) диалог: ходы те же, сессия своя. */
    adopt,
  };
}
