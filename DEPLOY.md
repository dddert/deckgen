# Запуск на сервере с RTX A6000

Целевой сервер: Linux, одна **NVIDIA RTX A6000 48 ГБ (Ampere, sm_86)**, доступ через Jupyter/SSH. Ставим всё в
домашнюю папку без root и без Docker (в v1 Docker на сервере был выключен). Порты слушают только `127.0.0.1`.

## Вариант 1 (основной): нативно, без root

Всё ставится в `DECKGEN_HOME` (по умолчанию `~/.deckgen2`): два venv (приложение и vLLM), веса модели, портативный
LibreOffice, шрифты, логи, результаты. Системные пакеты, `~/.bashrc` и сервисы не трогаются.

```bash
cd ~/deck-designer-v2
bash scripts/native/setup.sh          # ~20–40 мин: пакеты + ~29 ГБ весов Qwen3.8-27B-FP8
bash scripts/native/start.sh          # vLLM (ждёт готовности модели) + API/UI
bash scripts/native/status.sh         # состояние, память GPU, хвост лога vLLM
bash scripts/native/stop.sh
```

Только приложение, без модели (быстро, для проверки разбора и аудита): `bash scripts/native/setup.sh app`, затем
`LLM_PROVIDER=offline deckgen run ...`.

UI: `http://127.0.0.1:18080`. Снаружи — SSH-туннель `ssh -L 18080:127.0.0.1:18080 user@server` или
`jupyter-server-proxy`: `https://<jupyter>/user/<login>/proxy/18080/`. Интерфейс использует относительные пути,
поэтому под прокси работает.

### Параметры (переменные окружения перед `start.sh`, см. `scripts/native/env.sh`)

| переменная | по умолчанию | когда менять |
|---|---|---|
| `LLM_MODEL` | `Qwen/Qwen3.8-27B-FP8` | профиль скорости — W4A16-квантизация (MODELS.md) |
| `VLLM_GPU_UTIL` | `0.88` | `0.72`, если включён text-to-image; меньше, если на GPU есть другие процессы |
| `LLM_MAX_SEQS` | `16` | меньше при OOM на старте (состояние Gated DeltaNet на каждую последовательность) |
| `LLM_MAX_LEN` | `32768` | 16384, если не хватает памяти |
| `VLLM_MTP` | `0` | `1` — MTP speculative decoding, примерно в 1.5 раза быстрее генерация |
| `GPU_ID` | `0` | номер карты |
| `T2I_ENABLED` | `false` | `true` + `pip install -e ".[t2i]"` — FLUX.2-klein-4B для иллюстраций |

### Проверка

```bash
source scripts/native/env.sh && source "$APP_VENV/bin/activate"
deckgen parse "data/templates/Шаблон презентации VK Education.pptx"
bash scripts/demo_9_decks.sh                   # 9 колод для промежуточной сдачи
python scripts/benchmark.py                    # тайминги по этапам
DECKGEN_E2E_LLM=1 pytest -q tests/e2e          # e2e с моделью
```

## Вариант 2: Docker (если демон доступен)

Нужны `dockerd` и `nvidia-container-toolkit`.

```bash
cp .env.example .env
docker compose up -d --build
```

Данные (веса, результаты) лежат в `DECKGEN_DATA_DIR` (по умолчанию `./.deckgen`). Очистка: `docker compose down`.

## Топ-10: инференс VK

Меняются только адрес и ключ, модель та же (Qwen 3.8 27B), промпты и схемы переносятся как есть:

```bash
export LLM_BASE_URL=https://<inference-vk>/v1 LLM_API_KEY=<ключ> LLM_SERVED_NAME=<имя модели у провайдера>
export VLM_BASE_URL=$LLM_BASE_URL VLM_API_KEY=$LLM_API_KEY VLM_SERVED_NAME=$LLM_SERVED_NAME
```

Если провайдер не поддерживает `response_format: json_schema`, выставьте `DECKGEN__LLM__STRUCTURED_OUTPUT=false`:
останется валидация pydantic с повтором.

## Диагностика

| симптом | причина и что сделать |
|---|---|
| vLLM падает на старте с OOM / «not enough cache blocks» | уменьшить `LLM_MAX_SEQS` (16 → 8) или `LLM_MAX_LEN`; проверить, что GPU свободна (`nvidia-smi`) |
| `Qwen3_5ForConditionalGeneration not supported` | старая vLLM: `uv pip install -U vllm` в `$VLLM_VENV` или временно `LLM_MODEL=Qwen/Qwen3.6-27B-FP8` |
| в UI «без модели (эвристики)» | vLLM не отвечает на `LLM_BASE_URL`; колоды собираются эвристиками, лог — `$LOG_DIR/vllm.log` |
| нет PDF и VLM-аудита, «LibreOffice не найден» | `bash scripts/native/get_libreoffice.sh` (или `apt install libreoffice-impress` при наличии прав); проверить URL AppImage в скрипте |
| текст вылезает у брендового шрифта | `python scripts/fetch_fonts.py data/templates` — TTF гарнитур из Google Fonts в `assets/fonts` |
| генерация дольше 5 минут | `VLLM_MTP=1`; профиль W4A16; `DECKGEN__AUDIT__SEMANTIC_CHECKS=false`; см. тайминги в `run.json` |

## Полное удаление

```bash
bash scripts/native/stop.sh && source scripts/native/env.sh && rm -rf "$DECKGEN_HOME"
```
