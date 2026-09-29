# Общие переменные нативного запуска (без Docker, без root). Всё — в одной папке DECKGEN_HOME.
export DECKGEN_HOME="${DECKGEN_HOME:-$HOME/.deckgen2}"
export PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export APP_VENV="$DECKGEN_HOME/venv-app"
export VLLM_VENV="$DECKGEN_HOME/venv-vllm"
export HF_HOME="$DECKGEN_HOME/hf"
export DECKGEN_OUTPUTS="$DECKGEN_HOME/outputs"
export DECKGEN_LO_PROFILE="$DECKGEN_HOME/lo-profiles"
export LOG_DIR="$DECKGEN_HOME/logs"
export UV_CACHE_DIR="$DECKGEN_HOME/uv-cache"
export PATH="$DECKGEN_HOME/bin:$DECKGEN_HOME/libreoffice/program:$DECKGEN_HOME/node/bin:$PATH"

# --- Модель: RTX A6000 (48 ГБ, Ampere sm_86). См. MODELS.md.
export LLM_MODEL="${LLM_MODEL:-Qwen/Qwen3.8-27B-FP8}"      # FP8-веса -> Marlin W8A16 на Ampere, ~29 ГБ
export LLM_SERVED_NAME="${LLM_SERVED_NAME:-qwen}"
export LLM_MAX_LEN="${LLM_MAX_LEN:-32768}"
export LLM_MAX_SEQS="${LLM_MAX_SEQS:-16}"                  # у Qwen3.x GatedDeltaNet-состояние на каждую последовательность
export VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.88}"              # 0.72, если включён text-to-image (FLUX.2-klein рядом)
export VLLM_MTP="${VLLM_MTP:-0}"                            # 1 — MTP speculative decoding (~1.5× к скорости генерации)
export GPU_ID="${GPU_ID:-0}"
export LLM_PORT="${LLM_PORT:-18000}"
export API_PORT="${API_PORT:-18080}"
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:$LLM_PORT/v1}"
export VLM_BASE_URL="${VLM_BASE_URL:-$LLM_BASE_URL}"
mkdir -p "$DECKGEN_HOME" "$LOG_DIR" "$DECKGEN_OUTPUTS"
