#!/usr/bin/env bash
# Промежуточный этап ТЗ: один контент-пакет × 3 шаблона × 3 варианта = 9 презентаций (+ аудит, pdf, html).
set -euo pipefail
PACK=${1:-data/content_packs/example_product_launch}
for t in data/templates/*.pptx; do
  echo "=== $t"
  deckgen run "$t" "$PACK"
done
echo "результаты: ${DECKGEN_OUTPUTS:-./data/outputs}/runs/"
