#!/usr/bin/env bash
source "$(dirname "$0")/env.sh"
for n in api vllm; do
  f="$DECKGEN_HOME/$n.pid"
  [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null && echo "остановлен $n" ; rm -f "$f"
done
