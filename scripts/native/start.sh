#!/usr/bin/env bash
# Запуск vLLM (Qwen3.8-27B на RTX A6000) и API+UI в фоне; переживает закрытие терминала Jupyter.
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$PROJECT_DIR"

if ! curl -sf "http://127.0.0.1:$LLM_PORT/v1/models" >/dev/null; then
  SPEC=()
  [ "$VLLM_MTP" = "1" ] && SPEC=(--speculative-config '{"method":"mtp","num_speculative_tokens":2}')
  echo "== vLLM: $LLM_MODEL на GPU $GPU_ID (util $VLLM_GPU_UTIL)"
  CUDA_VISIBLE_DEVICES="$GPU_ID" nohup "$VLLM_VENV/bin/vllm" serve "$LLM_MODEL" \
    --served-model-name "$LLM_SERVED_NAME" --host 127.0.0.1 --port "$LLM_PORT" \
    --max-model-len "$LLM_MAX_LEN" --max-num-seqs "$LLM_MAX_SEQS" --gpu-memory-utilization "$VLLM_GPU_UTIL" \
    --limit-mm-per-prompt '{"image":1,"video":0}' --enable-prefix-caching --reasoning-parser qwen3 \
    "${SPEC[@]}" > "$LOG_DIR/vllm.log" 2>&1 &
  echo $! > "$DECKGEN_HOME/vllm.pid"
  echo -n "ждём модель"
  for _ in $(seq 1 180); do
    curl -sf "http://127.0.0.1:$LLM_PORT/v1/models" >/dev/null && break
    echo -n "."; sleep 5
  done
  echo
fi

echo "== API + UI на 127.0.0.1:$API_PORT"
nohup "$APP_VENV/bin/deckgen" serve --host 127.0.0.1 --port "$API_PORT" > "$LOG_DIR/api.log" 2>&1 &
echo $! > "$DECKGEN_HOME/api.pid"
sleep 2
curl -s "http://127.0.0.1:$API_PORT/api/health" || true
echo
echo "UI: http://127.0.0.1:$API_PORT  (снаружи — SSH-туннель или jupyter-server-proxy .../proxy/$API_PORT/)"
