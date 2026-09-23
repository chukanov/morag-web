#!/bin/sh
# Поставить «Загрузить запись» на Mac — БЕЗ администратора, Homebrew и учёток на стороне.
#
#   curl -fsSL https://<сайт>/api/ingest/get/<пропуск>/install | sh
#
# Всё приезжает с сайта (зеркало `ingest.dist_dir`): портативный питон, статический ffmpeg,
# снимок инструментов, движок транскрибации и модели — включая ту, что обычно требует токена
# Hugging Face и нажатия «Agree». Ставится в ОДИН каталог `~/morag-ingest`; удалить установку =
# удалить его (плюс приложение в ~/Applications). Повторный запуск досыпает недостающее:
# докачивает оборванное, пропускает готовое.
#
# ⚠️ Собранное ЗДЕСЬ приложение Gatekeeper не проверяет: карантин вешает тот, кто СКАЧАЛ файл, а
# `curl | sh` не помечает ничего. Поэтому ни «неизвестного разработчика», ни похода в настройки
# системы не будет. Ad-hoc-подпись (`codesign -s -`) нужна не для этого: без неё macOS считает
# приложение новым после каждого обновления и заново спрашивает доступ к папкам.
#
# Переменные: MORAG_INGEST_HOME — куда ставить, MORAG_SITE и MORAG_PASS — сайт и пропуск
# (подставляются сервером при выдаче), MORAG_NO_APP=1 — не собирать приложение,
# MORAG_NO_OPEN=1 — собрать, но не запускать (так проверяют установку).
set -eu

SITE="${MORAG_SITE:-@SITE@}"
PASS="${MORAG_PASS:-@TOKEN@}"
BUILT="@BUILT@"
ROOT="${MORAG_INGEST_HOME:-$HOME/morag-ingest}"
API="$SITE/api/ingest/get/$PASS"
DIST="$ROOT/dist"
STACK="$ROOT/asr-stack"
ENV_FILE="$ROOT/env"
PY="$ROOT/python/bin/python3"
APPS="$HOME/Applications"
APP="$APPS/Загрузить запись.app"
NEED_GB=15
STEPS=8

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m[%s/%s] %s\033[0m\n' "$1" "$STEPS" "$2"; }

# --- 1. годится ли машина ----------------------------------------------------------------
step 1 "проверяю машину"
[ "$(uname -s)" = Darwin ] || die "это установщик для macOS"
[ "$(uname -m)" = arm64 ] || die "нужен Mac на Apple Silicon (M1 и новее): на Intel модели не пойдут"
command -v curl >/dev/null 2>&1 || die "нет curl"
command -v shasum >/dev/null 2>&1 || die "нет shasum"
FREE_GB=$(df -g "$HOME" | awk 'NR==2 {print $4}')
[ "${FREE_GB:-0}" -ge "$NEED_GB" ] || die "на диске ${FREE_GB} ГБ, нужно хотя бы $NEED_GB ГБ"
ok "macOS на Apple Silicon, свободно ${FREE_GB} ГБ"
ok "ставлю в $ROOT (сайт $SITE)"
mkdir -p "$DIST" "$ROOT/bin" "$STACK"

# --- 2. зеркало --------------------------------------------------------------------------
# Опись читается СТРОКАМИ, а не как JSON: питона на чистом маке нет (системный `python3` —
# заглушка, которая просит поставить Xcode), и разбирать JSON тут нечем и незачем.
step 2 "опись зеркала"
curl -fsSL --retry 3 "$API/file/manifest.sh" -o "$DIST/manifest.sh" \
  || die "сайт не отдал опись зеркала. Ссылка на установку живёт неделю — если она старая, откройте страницу загрузки заново"
TOTAL=$(awk -F'|' '$1 !~ /^#/ && NF>=4 {s+=$4} END {printf "%.1f", s/1073741824}' "$DIST/manifest.sh")
ok "собрано ${BUILT:-—}, скачать ${TOTAL} ГБ (повторный запуск — только недостающее)"

# --- 3. скачать --------------------------------------------------------------------------
step 3 "скачиваю (можно прервать и запустить установку снова — продолжит с места)"
fetch() {                       # fetch <имя> <байт> <sha256> <подпись>
  name="$1"; size="$2"; sum="$3"; title="$4"; path="$DIST/$name"
  if [ -f "$path" ] && [ "$(wc -c < "$path" | tr -d ' ')" = "$size" ]; then
    ok "$title — уже скачано"; return 0
  fi
  printf '  %s (%s МБ)\n' "$title" "$((size / 1048576))"
  curl -fL --retry 5 --retry-delay 2 --progress-bar -C - -o "$path" "$API/file/$name" \
    || die "не скачалось: $title. Проверьте сеть и запустите команду снова — докачает"
  got=$(shasum -a 256 "$path" | cut -d' ' -f1)
  [ "$got" = "$sum" ] || { rm -f "$path"; die "$title скачался битым — запустите установку снова"; }
}
while IFS='|' read -r name unpack to size sum title; do
  case "$name" in ''|\#*) continue ;; esac
  fetch "$name" "$size" "$sum" "$title"
done < "$DIST/manifest.sh"
ok "всё на месте"

# --- 4. разложить ------------------------------------------------------------------------
# `to` в описи — куда класть: `root/…` в каталог установки, `stack/…` в стек транскрибации,
# `home/…` в домашний (там кэши, куда библиотеки ходят сами). Путь, кончающийся на `/`, —
# каталог, в него распаковывается архив; иначе это имя файла.
step 4 "раскладываю"
place() {                       # place <имя> <вид> <куда>
  name="$1"; unpack="$2"; to="$3"; src="$DIST/$name"
  case "$to" in
    root/*)  dest="$ROOT/${to#root/}" ;;
    stack/*) dest="$STACK/${to#stack/}" ;;
    home/*)  dest="$HOME/${to#home/}" ;;
    *) die "непонятное место в описи: $to" ;;
  esac
  case "$to" in */) mkdir -p "$dest" ;; *) mkdir -p "$(dirname "$dest")" ;; esac
  case "$unpack" in
    targz|tar) tar -xf "$src" -C "$dest" ;;
    file)      cp -f "$src" "$dest" ;;
    exec)      cp -f "$src" "$dest"; chmod 755 "$dest" ;;
    *) die "непонятный вид в описи: $unpack" ;;
  esac
}
while IFS='|' read -r name unpack to size sum title; do
  case "$name" in ''|\#*) continue ;; esac
  place "$name" "$unpack" "$to"
done < "$DIST/manifest.sh"
# Карантин на скачанном `curl` не появляется, но снять его дёшево, а разбираться с молчаливым
# «приложение повреждено» потом — дорого.
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true
[ -x "$PY" ] || die "в зеркале нет портативного питона ($PY)"
[ -x "$ROOT/bin/ffmpeg" ] || die "в зеркале нет ffmpeg"
ok "питон $("$PY" -V 2>&1 | cut -d' ' -f2), ffmpeg $("$ROOT/bin/ffmpeg" -version 2>/dev/null | head -1 | cut -d' ' -f3)"
export PATH="$ROOT/bin:$PATH"

# --- 5. настройки ------------------------------------------------------------------------
# Файл окружения — единственное место с личными данными: ваш ключ к шлюзу и ключи, которыми
# части стека разговаривают между собой на этой машине. 0600, никуда не уезжает.
step 5 "настройки"
if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/morag/services/asr-adaptor/deploy/mac/env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
setenv() {                      # setenv <ИМЯ> <значение> — заполняет строку шаблона или дописывает
  awk -v k="$1" -v v="$2" '
    BEGIN { done=0 }
    $0 ~ "^" k "=" && !done { print k "=" v; done=1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$ENV_FILE" > "$ENV_FILE.new" && mv "$ENV_FILE.new" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}
setenv ASR_STACK_HOME "$STACK"
setenv MORAG_REPO "$ROOT/morag"
setenv ASR_PYTHON "$PY"
setenv ASR_PROFILE "$ROOT/profile.env"
setenv MORAG_SITE "$SITE"
# Ключ к шлюзу спрашивает само приложение (в настройках): в терминал его вводить не заставляем,
# а в установщик он не зашит намеренно — ключ у каждого свой.
grep -q '^OR_KEY=..' "$ENV_FILE" || setenv OR_KEY ""
# Адрес и модель шлюза приезжают с зеркала (`gateway.env`) — это настройка корпуса, а не секрет;
# ключ к шлюзу у каждого свой и спрашивается в приложении.
# ⚠️ `a && b` под `set -e` — выход из скрипта, когда `a` ложно: поэтому только `if`.
if [ -f "$ROOT/gateway.env" ]; then
  . "$ROOT/gateway.env"
  if [ -n "${ASR_LLM_BASE_URL:-}" ]; then setenv ASR_LLM_BASE_URL "$ASR_LLM_BASE_URL"; fi
  if [ -n "${ASR_LLM_MODEL:-}" ]; then setenv ASR_LLM_MODEL "$ASR_LLM_MODEL"; fi
fi
ok "$ENV_FILE"

# --- 6. окружения питона -----------------------------------------------------------------
step 6 "окружения питона — самая долгая часть (около 3 ГБ пакетов, 10–20 минут)"
retry_pip() {                   # прокси в окружении бывает лишним: наш контур доступен напрямую
  if "$@"; then return 0; fi
  warn "не вышло — повторяю без прокси из окружения"
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy "$@"
}
retry_pip env ASR_STACK_ENV="$ENV_FILE" ASR_STACK_HOME="$STACK" MORAG_REPO="$ROOT/morag" \
  bash "$ROOT/morag/services/asr-adaptor/deploy/mac/install.sh" \
  || die "не собрался стек транскрибации — напишите тому, кто дал ссылку, и покажите последние строки"
VIDEO="$STACK/video-venv"
if [ ! -x "$VIDEO/bin/python" ]; then
  "$PY" -m venv "$VIDEO"
  retry_pip "$VIDEO/bin/pip" install -q --upgrade pip wheel
fi
retry_pip "$VIDEO/bin/pip" install -q -r "$ROOT/web/tools/requirements-video.txt" \
                                    -r "$ROOT/web/tools/requirements-app.txt"
ok "окружения готовы"

# --- 7. команда и приложение -------------------------------------------------------------
step 7 "приложение"
cat > "$ROOT/bin/morag-ingest" <<EOF
#!/bin/sh
# То же самое из терминала: morag-ingest ui | app | run видео.mp4 …
export ASR_STACK_ENV="$ENV_FILE" MORAG_REPO="$ROOT/morag" ASR_STACK_HOME="$STACK" MORAG_SITE="$SITE"
export PATH="$ROOT/bin:\$PATH"
exec "$VIDEO/bin/python" "$ROOT/web/tools/ingest.py" "\$@"
EOF
chmod 755 "$ROOT/bin/morag-ingest"

if [ -z "${MORAG_NO_APP:-}" ]; then
  mkdir -p "$APPS" "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/MacOS/launcher" <<EOF
#!/bin/sh
exec "$ROOT/bin/morag-ingest" app
EOF
  chmod 755 "$APP/Contents/MacOS/launcher"
  printf 'APPL????' > "$APP/Contents/PkgInfo"
  cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Загрузить запись</string>
  <key>CFBundleDisplayName</key><string>Загрузить запись</string>
  <key>CFBundleIdentifier</key><string>org.morag.ingest</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleIconFile</key><string>app</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
</dict></plist>
EOF
  # Значок — по желанию: картинка корпуса с зеркала. Нет её или нет системных `sips`/`iconutil` —
  # приложение просто с обычным значком, на работу это не влияет.
  if [ -f "$ROOT/icon.png" ] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
    ICONSET="$ROOT/dist/app.iconset"; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
    for s in 16 32 64 128 256 512; do
      sips -z $s $s "$ROOT/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1 || true
      sips -z $((s*2)) $((s*2)) "$ROOT/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1 || true
    done
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/app.icns" >/dev/null 2>&1 || true
    rm -rf "$ICONSET"
  fi
  # Ad-hoc-подпись: без неё macOS считает приложение НОВЫМ после каждого обновления и заново
  # спрашивает доступ к «Загрузкам» и «Рабочему столу».
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - "$APP" >/dev/null 2>&1 && ok "подписано (разрешения на папки спросятся один раз)" \
      || warn "не подписалось — доступ к папкам придётся разрешать после каждого обновления"
  fi
  ok "$APP"
fi

# --- 8. готово ---------------------------------------------------------------------------
step 8 "готово"
# Скачанное больше не нужно: модели и питон уже разложены по местам, а копия архивов — это
# ещё почти три гигабайта на ноутбуке. Опись оставляем: по ней видно, что и когда поставлено.
# ⚠️ Чистим ТОЛЬКО в самом конце: оборвавшаяся установка должна продолжаться с места, а не
# качать всё заново.
FREED=$(du -sm "$DIST" 2>/dev/null | cut -f1)
find "$DIST" -type f ! -name 'manifest.sh' -delete 2>/dev/null || true
ok "освободил ${FREED:-0} МБ скачанного (при повторной установке скачается снова)"
cat <<EOF

  Приложение «Загрузить запись» — в папке «Программы» вашей домашней папки
  (Finder → Переход → Личная папка → Applications; или Spotlight по слову «Загрузить»).

  Первый запуск: в настройках впишите свой ключ к шлюзу — где его взять, написано там же.
  Дальше: перетащите видео в окно, при желании поправьте название — и всё.

  То же из терминала:  $ROOT/bin/morag-ingest ui
  Удалить установку:   rm -rf "$ROOT" "$APP"
EOF
if [ -z "${MORAG_NO_APP:-}${MORAG_NO_OPEN:-}" ]; then open "$APP" 2>/dev/null || true; fi
