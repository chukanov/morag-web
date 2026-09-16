// Граф импортов фронта: каждый путь ведёт в существующий файл.
//
// Сборки у нас нет, модули грузит сам браузер — и опечатка в пути или ссылка на
// удалённый модуль роняет ВЕСЬ апп молча (ловили: удалили `icons.js`, а `view.js`
// продолжал его звать). Проверка дешёвая, а класс ошибок закрывает целиком.
//     node tests/js/imports.test.mjs
import { fileURLToPath } from "node:url";
import { dirname, join, resolve, relative } from "node:path";
import { readdir, readFile, access } from "node:fs/promises";

const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const webJs = join(repo, "web", "js");

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(path)));
    else if (entry.name.endsWith(".js")) out.push(path);
  }
  return out;
}

const IMPORT_RE = /(?:^|\n)\s*(?:import|export)[^'"\n]*from\s*["']([^"']+)["']/g;
const DYNAMIC_RE = /import\(\s*["']([^"']+)["']\s*\)/g;

const files = await walk(webJs);
let failures = 0;
let checked = 0;

for (const file of files) {
  const source = await readFile(file, "utf8");
  const specs = [
    ...[...source.matchAll(IMPORT_RE)].map((m) => m[1]),
    ...[...source.matchAll(DYNAMIC_RE)].map((m) => m[1]),
  ];
  for (const spec of specs) {
    if (!spec.startsWith(".") && !spec.startsWith("/")) continue; // внешних у нас нет
    checked += 1;
    const target = resolve(dirname(file), spec);
    try {
      await access(target);
    } catch {
      failures += 1;
      console.log(`  ✗ ${relative(repo, file)} → ${spec}  (нет файла)`);
    }
  }
}

// Отдельно: index.html грузит точку входа — если её путь врёт, не стартует ничего
const html = await readFile(join(repo, "web", "index.html"), "utf8");
for (const m of html.matchAll(/<script[^>]*\bsrc=["']([^"']+)["']/g)) {
  const src = m[1];
  if (src.startsWith("http")) continue;
  checked += 1;
  // ⚠️ Путь обязан быть АБСОЛЮТНЫМ. Адреса у нас вложенные
  // (/demo/rec/2026-03-12-kafka), и относительный `js/main.js` браузер ищет от текущего
  // пути — то есть в /demo/rec/js/main.js, которого нет. Приложение при этом
  // не стартует вовсе: белая страница с руганью про жёсткую перезагрузку.
  if (!src.startsWith("/")) {
    failures += 1;
    console.log(`  ✗ index.html → ${src}  (путь относительный: сломается на /<slug>/rec/<id>)`);
    continue;
  }
  try {
    await access(join(repo, "web", src.replace(/^\//, "")));
  } catch {
    failures += 1;
    console.log(`  ✗ index.html → ${src}  (нет файла)`);
  }
}

console.log(failures ? `\n${failures} битых ссылок из ${checked}` : `все ${checked} ссылок ведут в файлы (${files.length} модулей)`);
process.exit(failures ? 1 : 0);
