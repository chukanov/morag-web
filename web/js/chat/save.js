// «Сохранить диалог» — целиком в браузере: сервер об этом не знает.
// Markdown выбран под науч-аудиторию: Obsidian, заметки, вставка в статьи.
import { fmt, toast } from "../ui/dom.js";

/** @param {{question:string, answer:string, citations:Array}[]} turns */
export function saveDialogMd(turns, { corpus = "", topic = null } = {}) {
  if (!turns?.length) {
    toast("Пока нечего сохранять");
    return;
  }
  const date = new Date().toISOString().slice(0, 10);
  const records = [...new Set(turns.flatMap((t) => (t.citations || []).map((c) => c.rec)).filter(Boolean))];

  const lines = ["---"];
  if (corpus) lines.push(`корпус: ${corpus}`);
  if (topic?.title) lines.push(`тема: ${topic.title}`);
  lines.push(`дата: ${date}`, `вопросов: ${turns.length}`);
  if (records.length) lines.push(`записи: [${records.join(", ")}]`);
  lines.push("---", "");
  if (topic?.title) lines.push(`# ${topic.title}`, topic.summary || "", "");

  turns.forEach((turn, i) => {
    lines.push(`## ${i + 1}. ${turn.question.replace(/\n/g, " ")}`, "", turn.answer.trim(), "");
    if (turn.citations?.length) {
      lines.push("**Источники:**", "");
      for (const c of turn.citations) {
        const label = (c.label || "").split(" · ")[0];
        // ссылка живая: `#t=сек` откроет запись с нужной секунды
        lines.push(`${c.n}. **${label}** · ${fmt(c.sec || 0)} — [слушать](${c.url})`);
      }
      lines.push("");
    }
  });

  const slug = (topic?.title || corpus || "диалог").toLowerCase().replace(/\s+/g, "-").slice(0, 40);
  download(`${slug}-${date}.md`, lines.join("\n"));
  toast("Диалог сохранён");
}

function download(filename, text) {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
