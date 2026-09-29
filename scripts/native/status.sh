#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"
echo "vLLM: $(curl -sf http://127.0.0.1:$LLM_PORT/v1/models >/dev/null && echo up || echo down)"
echo "API:  $(curl -sf http://127.0.0.1:$API_PORT/api/health || echo down)"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv
tail -n 3 "$LOG_DIR/vllm.log" 2>/dev/null
