// Страница «Загрузить свою запись»: откуда взять приложение для мака и что дальше.
//
// Зачем страница, если есть приложение. Человек, у которого есть запись, приходит на сайт, а не
// в репозиторий: страница — единственное место, где он вообще узнаёт, что запись можно выложить
// самому. Поэтому здесь ровно два действия («скачать файл» и «строка в терминале») и честные
// числа: сколько ждать и сколько места нужно.
//
// ⚠️ Оба пути ведут в ОДИН установщик — сервер подставляет в него свой адрес и пропуск
// (`app/api/ingest.py`, `app/content/dist.py`). Строка живёт неделю; страница это говорит,
// потому что просроченную ссылку человек иначе понесёт в поддержку.
//
// Раздачи может не быть вовсе (публичная платформа, разработка): тогда страница честно говорит,
// что зеркала нет, и показывает путь для тех, у кого есть репозиторий.
import { $, el, copyLink, toast } from "./ui/dom.js";
import { getDist } from "./api.js";

const GB = 1024 ** 3;

/** «2.8 ГБ» — одна цифра после запятой: точность здесь не нужна, порядок важен. */
const gb = (bytes) => `${(bytes / GB).toFixed(1)} ГБ`;

function steps() {
  return el("ol", { class: "ing-steps" },
    el("li", {}, el("b", { text: "Поставить" }), " — 20–40 минут: скачается всё нужное и соберутся окружения. Пароль администратора и Homebrew не нужны."),
    el("li", {}, el("b", { text: "Войти" }), " — своей учётной записью, прямо в приложении. Ключи и адреса оно пропишет само: стадии с ИИ пойдут через сайт от вашего имени."),
    el("li", {}, el("b", { text: "Перетащить видео" }), " в окно. Название придумается само, если не задать; категория, темы и аннотация появятся на сайте."),
  );
}

function command(line) {
  const code = el("code", { class: "ing-cmd", text: line });
  const copy = el("button", { class: "dl-btn", type: "button", text: "Скопировать" });
  copy.addEventListener("click", () => copyLink(line, "Команда скопирована"));
  return el("div", { class: "ing-cmdrow" }, code, copy);
}

/** «питон, ffmpeg, движок и 4 модели» — перечислять модели поимённо незачем: их читает машина. */
function composition(files = []) {
  const models = files.filter((f) => f.title.startsWith("модель"));
  const rest = files.filter((f) => !f.title.startsWith("модель") && !f.title.includes("значок"));
  const tail = models.length ? `${rest.map((f) => f.title).join(", ")} и ${models.length} модели` : rest.map((f) => f.title).join(", ");
  return `В составе: ${tail}.`;
}


export async function renderIngest() {
  document.body.setAttribute("data-view", "ingest");
  const mount = $("#ingest-body");
  if (!mount) return;
  mount.replaceChildren(el("p", { class: "ing-note", text: "Смотрю, что готово…" }));

  let data = null;
  let failed = "";
  try {
    data = await getDist();
  } catch (error) {
    failed = error?.status === 404 ? "no-mirror" : "error";
  }

  if (failed) {
    mount.replaceChildren(
      el("p", { class: "ing-note", text: failed === "no-mirror"
        ? "На этом сайте раздача приложения не настроена."
        : "Не получилось спросить сервер о раздаче — попробуйте обновить страницу." }),
      el("p", { class: "ing-note" },
        "Всё то же самое умеет командная строка: ",
        el("code", { text: "python3 tools/ingest.py ui" }),
        " из чекаута платформы."),
    );
    return;
  }

  const zip = el("a", { class: "ing-get", href: data.app, text: "Скачать для Mac" });
  mount.replaceChildren(
    el("p", { class: "ing-lead" },
      "Расшифровка идёт ", el("b", { text: "у вас на маке" }),
      " — сервера с моделями у сайта нет. Приложение скачивает всё нужное отсюда же: ни Homebrew, ни прав администратора, ни учёток на стороне."),
    el("p", { class: "ing-note", text: "Нужен Mac на Apple Silicon (M1 и новее) и 15 ГБ свободного места." }),

    el("div", { class: "ing-get-row" }, zip,
      el("span", { class: "ing-note", text: `при установке приедет ${gb(data.bytes || 0)}` })),
    el("p", { class: "ing-note" },
      "Распакуйте и откройте «Установить…». При первом запуске macOS спросит про скачанный файл: ",
      el("b", { text: "правый щелчок → «Открыть»" }), " — это она спрашивает обо всём, что пришло из сети."),

    el("p", { class: "ing-note ing-or", text: "Или одной строкой в Терминале — тогда не спросит ничего:" }),
    command(data.install),
    el("p", { class: "ing-note", text: `Ссылка на установку живёт ${data.days} дней — потом откройте эту страницу заново.` }),

    el("h3", { class: "ing-h", text: "Как это выглядит" }),
    steps(),
    el("p", { class: "ing-note", text: "Час записи — примерно 15–25 минут работы на ноутбуке: расшифровка, разметка говорящих, при желании разбор экрана из видео. Дальше сайт соберёт запись сам." }),
    el("p", { class: "ing-note ing-built" }, composition(data.files), data.built ? ` Собрано ${data.built}.` : ""),
  );
  if ((data.files || []).some((f) => f.missing)) {
    mount.append(el("p", { class: "ing-warn", text: "⚠️ Зеркало неполное — установка остановится. Напишите тому, кто дал ссылку." }));
    toast("Зеркало на сервере неполное");
  }
}
