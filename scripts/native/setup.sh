#!/usr/bin/env bash
# Установка в домашнюю папку (без root, без Docker). Один раз: ~20–40 минут (пакеты + ~29 ГБ весов).
#   bash scripts/native/setup.sh          — всё
#   bash scripts/native/setup.sh app      — только приложение (без vLLM и весов)
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_DIR"

if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$DECKGEN_HOME/bin" INSTALLER_NO_MODIFY_PATH=1 sh
fi

echo "== приложение"
uv venv -q --python 3.12 "$APP_VENV"
uv pip install -q --python "$APP_VENV/bin/python" -e ".[dev,docs]"
[ "${T2I_ENABLED:-false}" = "true" ] && uv pip install -q --python "$APP_VENV/bin/python" -e ".[t2i]"

echo "== шрифты шаблонов (Google Fonts по гарнитурам из data/templates) — точная подгонка текста"
"$APP_VENV/bin/python" scripts/fetch_fonts.py data/templates || echo "шрифты не скачаны — будет прокси-метрика"

if ! command -v soffice >/dev/null; then
  echo "== LibreOffice (портативная сборка в $DECKGEN_HOME/libreoffice) — рендер PNG/PDF и VLM-аудит"
  bash scripts/native/get_libreoffice.sh || echo "LibreOffice не установлен: PDF/PNG и VLM-аудит будут пропущены"
fi

if command -v npm >/dev/null || [ -x "$DECKGEN_HOME/node/bin/npm" ]; then
  echo "== веб-интерфейс"
  (cd web && npm ci --no-audit --no-fund && npm run build)
fi

[ "${1:-all}" = "app" ] && { echo "готово (без модели)"; exit 0; }

echo "== vLLM (отдельное окружение, чтобы не конфликтовать с приложением)"
uv venv -q --python 3.12 "$VLLM_VENV"
uv pip install -q --python "$VLLM_VENV/bin/python" "vllm>=0.30" "huggingface_hub[cli]"
echo "== веса $LLM_MODEL (Apache 2.0) -> $HF_HOME"
"$VLLM_VENV/bin/huggingface-cli" download "$LLM_MODEL" --exclude "*.pth" >/dev/null
echo "готово: bash scripts/native/start.sh"
