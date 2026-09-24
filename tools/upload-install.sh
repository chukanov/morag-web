#!/usr/bin/env bash
# Поставить на Mac (Apple Silicon) всё, чтобы транскрибировать свою запись и загрузить её на сайт
# (`tools/upload.py`). Идемпотентен: повторный запуск досыпает недостающее.
#
#   ./tools/upload-install.sh                 # всё: brew-зависимости, чекаут morag, стек, модели, video-venv
#   ./tools/upload-install.sh --check         # ничего не менять, только сказать, чего не хватает
#   ./tools/upload-install.sh --no-models     # без скачивания моделей (несколько ГБ) — сделать позже
#
# Что появится: чекаут движка `morag` рядом с этим репозиторием (стек транскрибации живёт там),
# `~/asr-stack` (venv-ы, модели, состояние), `~/.asr-stack.env` (адрес и ключ LLM, HF-токен;
# 0600 — это ваши секреты), `~/asr-stack/video-venv` (инструменты экрана и сам upload.py),
# `~/asr-stack/bin/morag-upload` — короткая команда.
#
# Модели: whisper и CAM++ — публичные, pyannote — с huggingface под вашим токеном (HF_TOKEN в
# env-файле) после принятия условий модели на её странице. Зеркало моделей вместо HF — позже.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB="$(cd "$HERE/.." && pwd)"
MORAG_REPO="${MORAG_REPO:-$(cd "$WEB/.." && pwd)/morag}"
STACK_HOME="${ASR_STACK_HOME:-$HOME/asr-stack}"
ENV_FILE="${ASR_STACK_ENV:-$HOME/.asr-stack.env}"
CHECK=0; MODELS=1
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --no-models) MODELS=0 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "неизвестный флаг: $a" >&2; exit 2 ;;
  esac
done
say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo "нужен Mac на Apple Silicon" >&2; exit 1; }

say "brew-зависимости: ffmpeg, python@3.12, git"
if ! command -v brew >/dev/null; then
  echo "нет Homebrew: https://brew.sh — поставьте и запустите снова" >&2; exit 1
fi
for pkg in ffmpeg python@3.12 git; do
  if brew list --versions "$pkg" >/dev/null 2>&1; then ok "$pkg"; else
    [[ $CHECK -eq 1 ]] && warn "$pkg — нет" || brew install "$pkg"
  fi
done
PY="$(brew --prefix)/opt/python@3.12/bin/python3.12"
[[ -x "$PY" ]] || PY="$(command -v python3)"

say "чекаут движка morag — $MORAG_REPO"
if [[ -d "$MORAG_REPO/services/asr-adaptor" ]]; then ok "есть"; else
  [[ $CHECK -eq 1 ]] && warn "нет" || git clone https://github.com/catonmoon/morag "$MORAG_REPO"
fi

say "стек транскрибации (install.sh морага)"
MAC="$MORAG_REPO/services/asr-adaptor/deploy/mac"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ $CHECK -eq 1 ]]; then warn "нет $ENV_FILE"; else
    cp "$MAC/env.example" "$ENV_FILE"; chmod 600 "$ENV_FILE"
    warn "создан $ENV_FILE из шаблона — впишите ASR_LLM_BASE_URL, ASR_LLM_MODEL, OR_KEY (ключ LLM), HF_TOKEN"
    read -r -p "адрес LLM-шлюза (OpenAI-совместимый, например https://llm.example.org/api): " LLM_URL
    read -r -p "модель: " LLM_MODEL
    read -r -s -p "ключ к шлюзу: " LLM_KEY; echo
    read -r -s -p "HF_TOKEN (для моделей pyannote; пусто — впишете потом): " HF; echo
    "$PY" - "$ENV_FILE" "$LLM_URL" "$LLM_MODEL" "$LLM_KEY" "$HF" "$MORAG_REPO" <<'PY'
import re, sys
path, url, model, key, hf, repo = sys.argv[1:]
text = open(path, encoding="utf-8").read()
def put(name, value):
    global text
    if not value: return
    if re.search(rf"^{name}=", text, re.M):
        text = re.sub(rf"^{name}=.*$", f"{name}={value}", text, count=1, flags=re.M)
    else:
        text += f"\n{name}={value}\n"
put("ASR_LLM_BASE_URL", url); put("ASR_LLM_MODEL", model); put("OR_KEY", key); put("HF_TOKEN", hf)
put("MORAG_REPO", repo); put("ASR_ENABLE_NAMING", "0")
open(path, "w", encoding="utf-8").write(text)
PY
  fi
fi
if [[ $CHECK -eq 1 ]]; then ASR_STACK_ENV="$ENV_FILE" "$MAC/install.sh" --check || true; else ASR_STACK_ENV="$ENV_FILE" "$MAC/install.sh"; fi

if [[ $MODELS -eq 1 ]]; then
  say "модели (whisper, CAM++, pyannote — несколько ГБ; повторно не качаются)"
  [[ $CHECK -eq 1 ]] || ASR_STACK_ENV="$ENV_FILE" "$MAC/fetch-models.sh"
fi

say "video-venv — инструменты экрана и upload.py"
VENV="$STACK_HOME/video-venv"
if [[ -x "$VENV/bin/python" ]]; then ok "есть"; elif [[ $CHECK -eq 0 ]]; then
  "$PY" -m venv "$VENV"; "$VENV/bin/pip" install -q -r "$HERE/requirements-video.txt"
fi

say "команда morag-upload и ярлык для Finder"
if [[ $CHECK -eq 0 ]]; then
  mkdir -p "$STACK_HOME/bin"
  cat > "$STACK_HOME/bin/morag-upload" <<EOF
#!/bin/sh
export ASR_STACK_ENV="$ENV_FILE" MORAG_REPO="$MORAG_REPO"
exec "$VENV/bin/python" "$HERE/upload.py" "\$@"
EOF
  chmod +x "$STACK_HOME/bin/morag-upload"
  # Ярлык, который открывается двойным щелчком: страница вместо командной строки. `.command` —
  # родной для macOS способ «файл, запускающий программу»: Finder отдаёт его Терминалу, тот
  # поднимает локальный сервер и открывает браузер.
  mkdir -p "$HOME/Applications"
  cat > "$HOME/Applications/Загрузить запись.command" <<EOF
#!/bin/sh
exec "$STACK_HOME/bin/morag-upload" ui
EOF
  chmod +x "$HOME/Applications/Загрузить запись.command"
fi
ok "~/Applications/Загрузить запись.command — двойной щелчок открывает страницу"
ok "$STACK_HOME/bin/morag-upload — то же из терминала (добавьте $STACK_HOME/bin в PATH)"
echo
echo "дальше:  двойной щелчок по «Загрузить запись» в ~/Applications — и заполнить форму"
echo "         (из терминала то же: morag-upload ui; совсем без страницы — morag-upload run …)"
