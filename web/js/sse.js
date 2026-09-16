// Чтение нашего SSE-потока.
//
// Намеренно fetch + ReadableStream, а не EventSource: последний умеет только GET
// (историю и контекст не передать) и, главное, сам переподключается — каждый
// сетевой чих запускал бы платный агентский цикл заново.

export async function* readFrames(response, signal) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const abort = () => reader.cancel().catch(() => {});
  signal?.addEventListener("abort", abort, { once: true });

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const frame = parseBlock(block);
        if (frame) yield frame;
      }
    }
    buffer += decoder.decode();
    const tail = parseBlock(buffer);       // хвост без завершающей пустой строки
    if (tail) yield tail;
  } finally {
    signal?.removeEventListener("abort", abort);
  }
}

function parseBlock(block) {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))     // строки с `:` — комментарии-пульс
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}
