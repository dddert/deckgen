# deckgen v2 — Цифровой дизайнер презентаций

Сервис читает чужой `.pptx`-шаблон как набор правил и собирает по тексту пользователя **три варианта** колоды в
стиле этого шаблона. Слайд — это **клон слайда-образца шаблона с правками**: текст в стиле образца, нужное число
карточек, нативный график или таблица на месте примера, картинки из материалов или text-to-image. Аудит встроен
в пайплайн и проверяет готовый файл. Экспорт в `.pptx` (нативные объекты), `.pdf`, `.html`.

Кейс VK Tech «Цифровой дизайнер презентаций». Документация:
[ARCHITECTURE](ARCHITECTURE.md) — пайплайн и границы слоёв · [MODELS](MODELS.md) — модели и требования ·
[AUDIT](AUDIT.md) — проверки и покрытие · [DEPLOY](DEPLOY.md) — сервер с RTX A6000 ·
[REVIEW](REVIEW.md) — разбор v1 и что изменено · [SKILLS](SKILLS.md) — скиллы агентов и источники.

## Что умеет

- Разбирает **любой** шаблон: слайды-образцы (как в датасете) или только макеты (чистый шаблон PowerPoint —
  создаются виртуальные образцы). Извлекает палитру, гарнитуры, шкалу кеглей, типы слайдов, карточки, место под
  график.
- Принимает **текст пользователя** и файлы (csv/xlsx/md/txt/docx/pdf/картинки) или папку контент-пакета.
- Строит каркас (заголовки-выводы, 10–15 слайдов) и пишет тексты под ёмкость конкретных слотов шаблона. Цифры —
  только из материалов, со сверкой.
- Три варианта вёрстки: **Сбалансированный / Визуальный / Компактный** (ось «плотность ↔ визуальность»).
- Графики и таблицы — нативные объекты в цветах и шрифтах шаблона, с учётом тёмного и светлого фона.
- Аудит: 26 детерминированных проверок (контраст по APCA, текст на графике по карте занятости) + 14 VLM-вопросов (11 Приложения 1 + 3 о дизайне). Автоисправления и исправления на выбор в UI.
- Работает и без модели (эвристический режим): для демо без GPU и как запасной путь при сбое инференса.

## Быстрый старт

### Локально, без GPU (эвристический режим)

```bash
uv venv && uv pip install -e ".[dev,docs]"        # или: pip install -e ".[dev,docs]"
export LLM_PROVIDER=offline VLM_PROVIDER=offline
deckgen parse "data/templates/VK Tech шаблон.pptx"
deckgen run "data/templates/VK Tech шаблон.pptx" data/content_packs/example_product_launch
pytest -q                                          # 30+ тестов, без модели и LibreOffice
```

UI: `cd web && npm ci && npm run build`, затем `deckgen serve` и http://127.0.0.1:18080.

### Сервер с RTX A6000 (Qwen3.8-27B в vLLM)

```bash
bash scripts/native/setup.sh      # один раз: venv, vLLM, веса, LibreOffice, шрифты, UI
bash scripts/native/start.sh      # vLLM + API/UI в фоне
bash scripts/native/status.sh
```

Подробно — [DEPLOY.md](DEPLOY.md). Docker: `cp .env.example .env && docker compose up -d --build`.

### CLI

| команда | что делает |
|---|---|
| `deckgen parse <шаблон.pptx> [--debug]` | разбор шаблона: палитра, шрифты, типы образцов, пригодность |
| `deckgen run <шаблон> <папка-пакета или .txt/.md> [--variant balanced] [--slides 12]` | сквозной прогон: 3 варианта + аудит + экспорт |
| `deckgen fix <run_dir> <variant> --finding <id\|check_id\|fix_id>` | применить исправления и пересобрать |
| `deckgen audit-catalog --markdown` | каталог проверок (источник для AUDIT.md) |
| `deckgen prompts` | агенты и скиллы с закреплёнными версиями |
| `deckgen serve` | API + UI |
| `bash scripts/demo_9_decks.sh` | 3 шаблона × 3 варианта = 9 колод (промежуточная сдача) |
| `python scripts/benchmark.py` | тайминги по этапам (бюджет ТЗ — 5 минут) |

## Переменные окружения

Все параметры — в [config.yaml](config.yaml) (единый конфиг воспроизводимого запуска). `${VAR:-default}` берётся
из окружения. Любой ключ переопределяется так: `DECKGEN__SECTION__KEY=value` (например, `DECKGEN__AUDIT__SEMANTIC_CHECKS=false`).

| переменная | назначение | по умолчанию |
|---|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_SERVED_NAME` | OpenAI-совместимый endpoint (vLLM или инференс VK) | `http://localhost:18000/v1`, `none`, `qwen` |
| `VLM_BASE_URL`, `VLM_API_KEY`, `VLM_SERVED_NAME` | endpoint для картинок (та же модель) | как у LLM |
| `LLM_PROVIDER`, `VLM_PROVIDER` | `openai_compat` или `offline` | `openai_compat` |
| `T2I_ENABLED` | text-to-image FLUX.2-klein-4B | `false` |
| `PDF_BACKEND`, `SOFFICE_BIN` | рендер: `auto` / `soffice` / `powerpoint` (macOS) / `none` | `auto`, `soffice` |
| `DECKGEN_OUTPUTS` | папка прогонов и кэша | `./data/outputs` |
| `API_HOST`, `API_PORT` | адрес API/UI | `127.0.0.1`, `18080` |
| `DECKGEN_CONFIG` | путь к конфигу | `config.yaml` |
| для vLLM (`scripts/native/env.sh`): `LLM_MODEL`, `LLM_MAX_LEN`, `LLM_MAX_SEQS`, `VLLM_GPU_UTIL`, `VLLM_MTP`, `GPU_ID` | параметры сервера модели | `Qwen/Qwen3.8-27B-FP8`, 32768, 16, 0.88, 0, 0 |

## Вход

- **Текст** в UI или файл `.txt`/`.md` в CLI. Предложения с цифрами становятся фактами — источником цифр.
- **Файлы**: `.csv`/`.xlsx` → таблицы для графиков; `.md`/`.txt`/`.docx`/`.pdf` → текст; картинки → в слоты фото.
- **Папка контент-пакета** (формат v1): `brief.md` (YAML front-matter: title, purpose, audience, target_slides,
  must_include) + `facts.yaml` + `data/*.csv` + `assets/`. Пример: [data/content_packs/example_product_launch](data/content_packs/example_product_launch).

## Выход — `data/outputs/runs/<дата>_<шаблон>_<пакет>/`

`pack.json`, `outline.json` (каркас), и для каждого варианта: `deck_<v>.pptx|pdf|html`, `plan.json` (какой образец
взят и что в нём изменено), `audit.json` (замечания с рамками, что исправлено, версии агентов), `png/` (рендер).

## Ограничения

- Только десктоп. UI проверен в Chrome, Firefox, Safari, Яндекс Браузере (без UI-библиотек, стандартный CSS).
- Модели — только открытые веса Apache 2.0/MIT ≤ 35B (text-to-image ≤ 20B), см. MODELS.md.
- Слайд никогда не выгружается растром. Картинки — только ассеты и сгенерированные иллюстрации в слотах.
- SmartArt образцов не редактируется: такие образцы исключаются. Сложные схемы (Гант, календарь, коннекторы)
  используются с пониженной уверенностью или исключаются.
- Демо-фото, зашитые **в макет** (а не в слайд), заменить нельзя. Такие образцы селектор почти не выбирает.
- Точная подгонка текста требует TTF гарнитуры шаблона. `scripts/fetch_fonts.py` берёт её с Google Fonts. Иначе
  используется прокси-метрика с запасом 6%.
- PDF, PNG и VLM-аудит требуют LibreOffice. Без него работают .pptx, .html и детерминированный аудит.
- Бюджет 5 минут на колоду рассчитан на A6000 (MODELS.md). Превышение записывается в `run.json` и показывается в UI.
