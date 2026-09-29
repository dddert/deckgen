# Скиллы: что подключено к модели и откуда

## Скиллы агентов (`skills/`, версионируются как промпты)

Скилл — блок знаний, который подставляется в промпт агента (`{{ skills }}`). В `prompts/registry.yaml` у каждой
версии агента явно указано, какие скиллы и каких версий она использует. Фактические версии записываются в
`plan.json` и `audit.json`.

| скилл | что даёт модели | агенты | источник идей (лицензия) |
|---|---|---|---|
| `storytelling/v1` | пирамида Минто, заголовки-выводы, «тест призрачной колоды», связность соседних слайдов, финал — призыв к действию | skeleton, rewrite | [Gabberflast/academic-pptx-skill](https://github.com/Gabberflast/academic-pptx-skill) (MIT): action titles, ghost deck test; [Akxan/ppt-agent-skill](https://github.com/Akxan/ppt-agent-skill) (MIT): pyramid principle |
| `density/v1` | лимиты плотности Приложения 1: пункты, слова, таблицы, серии, заполнение, без заглушек | skeleton, slide_writer | ТЗ VK Tech, Приложение 1 |
| `dataviz/v1` | выбор графика по данным, несопоставимые серии, подписи и единицы | skeleton | [Akxan/ppt-agent-skill](https://github.com/Akxan/ppt-agent-skill) (MIT): chart selection; правила ТЗ |
| `template_fit/v1` | как писать в слоты чужого шаблона: лимиты, параллельные карточки, цифры только из источников, не переносить демо-текст | slide_writer, shorten, rewrite | [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) (MIT): edit-based генерация по слайдам-образцам |
| `visual_qa/v1` | как отвечать на вопросы визуальной проверки: «нет того, о чём вопрос» = да, сверка цифр в любом написании | audit_semantic v1 | ТЗ, Приложение 1; PPTEval из PPTAgent (Content / Design / Coherence) |
| `visual_qa/v2` | то же + вопросы дизайна 12–14: что считать «текстом на графике», «плохо читается» (тёмный на насыщенном синем), дисбалансом | audit_semantic v2 | APCA ([Myndex/apca-w3](https://github.com/Myndex/apca-w3)); PPTEval |
| `literacy/v1` | грамотность и точность цифр: орфография, пунктуация, согласование, только кириллица в русских словах, типографика (тире, «ёлочки», десятичная запятая), фраза не обрывается на предлоге, цифры ровно как в фактах, нумерация по порядку, самопроверка корректора | slide_writer v3, shorten v2, rewrite v2, proofread v1 | нормы русской орфографии и пунктуации; [Бюро Горбунова](https://bureau.ru/) — типографика |
| `visual_design/v1` | как устроен хороший дизайн слайда и что в нём зависит от текста: одна мысль, крупный кегль ≤ 2 строк, воздух, параллелизм, «рядом графика — не длиннее образца», русская типографика (без точки в заголовках, тире только грамматическое, без «…»), запрет пустых слов и выдуманных цифр, самопроверка | slide_writer v2 | [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill) (MIT): иерархия, один акцент, «AI-клише»; Anthropic `canvas-design`: «ничего не налезает, у каждого элемента воздух»; [Бюро Горбунова](https://bureau.ru/soviet/20201007/): точки в заголовках, [вёрстка в чужом шаблоне](https://bureau.ru/bb/soviet/20170517/); [Butterick, Practical Typography](https://practicaltypography.com/presentations.html) |

Тексты скиллов написаны заново под задачу, дословно чужие тексты не копировались. Из репозиториев взяты
подходы, источники указаны.

### Как добавить скилл
1. `skills/<имя>/v1.md` — текст скилла (коротко и проверяемо).
2. `prompts/registry.yaml` → `skills.<имя>.versions.v1` и новая версия агента со списком `skills: [..., <имя>/v1]`.
3. Закрепить версию агента в `config.yaml: prompts.<агент>`. Прогнать `pytest` и `scripts/benchmark.py`.

## Что взято из исследованных репозиториев в архитектуру (а не только в промпты)

| репозиторий | идея | где в v2 |
|---|---|---|
| **icip-cas/PPTAgent** (MIT) | генерация правкой слайдов-образцов, а не с нуля; функциональные типы образцов; редактирование через операции (replace/delete/clone) с проверкой | `parsing/analyzer.py` (типы, элементы), `render/pptx_builder.py` (клон + правки), `composing/composer.py` |
| **PPTAgent → PPTEval** | оценка по Content / Design / Coherence | детерминированные проверки (Design) + VLM-вопросы (Content, Coherence) |
| **academic-pptx-skill** (MIT) | заголовок = вывод, история читается по заголовкам | скилл `storytelling`, VLM-вопрос 1, агент `rewrite` |
| **ppt-agent-skill** (MIT) | пирамида при планировании, «underfill» как дефект | скилл `storytelling`, проверка `density.fill_ratio` |
| скилл `pptx` среды разработки (Anthropic) | «дублируй слайд шаблона и правь XML, не пересоздавай»; «слотов шаблона больше, чем данных, — удали карточку целиком»; обязательная проверка заглушек и визуальный QA по рендеру | принципы клонирования, удаление элементов целиком, `integrity.leftover_placeholder`, рендер для аудита |

## Скиллы, использованные при разработке (Claude Code в этом проекте)

| скилл / инструмент | зачем |
|---|---|
| **graphify** | граф знаний по ТЗ (`graphify-out/`): требования и критерии запрашивались через `graphify query`; после изменений кода граф обновлён |
| **pptx** | методика работы с шаблонами (клонирование, чистка, QA по рендеру) |
| **canvas-design** (Anthropic, `.claude/skills/canvas-design`) | установлен из `ЛЦТ/SKILL.md`; принципы «ничего не налезает, у каждого элемента воздух, мастерская выделка» перенесены в скилл Qwen `visual_design` и в карту занятости |
| **taste-skill** (Leonxlnx, MIT; 13 скиллов в `.claude/skills/`: design-taste-frontend, design-taste-frontend-v1, gpt-taste, high-end-visual-design, minimalist-ui, industrial-brutalist-ui, redesign-existing-projects, brandkit, stitch-design-taste, image-to-code, imagegen-frontend-web/-mobile, full-output-enforcement) | установлены для Claude Code. Это скиллы веб-дизайна; для слайдов адаптированы только применимые правила (иерархия весом, один акцент и замок темы, запрет пустых слов и выдуманных «точных» цифр, проверка контраста). Правило «никаких тире» taste-skill для русского не годится (тире грамматическое) — в `visual_design` запрещено только декоративное «Тема — подтема» |
| **headroom** | установлен (прокси сжатия контекста), но сессии десктоп-приложения его обходят (`headroom doctor`: «Desktop routing is not supported yet»). Сжатие работает в терминальном `claude` после `headroom wrap claude`; в этой сессии не применялось |
| **markitdown** (MCP) | быстрое чтение текста шаблонов по слайдам |
| рендер через PowerPoint (AppleScript) | визуальная проверка всех шаблонов и колод на macOS без LibreOffice; оставлен в коде как запасной рендер (`PDF_BACKEND=powerpoint`) |
