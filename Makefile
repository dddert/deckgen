.PHONY: install web test lint serve demo parse catalog bench

install:            ## приложение + dev-зависимости
	pip install -e ".[dev,docs]"
web:                ## собрать UI (web/dist раздаёт FastAPI)
	cd web && npm ci && npm run build
test:               ## unit + e2e без модели и GPU
	pytest -q
lint:
	ruff check src tests
serve:
	deckgen serve
parse:              ## make parse T="data/templates/VK Tech шаблон.pptx"
	deckgen parse "$(T)"
demo:               ## 3 шаблона × 3 варианта = 9 колод
	bash scripts/demo_9_decks.sh
catalog:            ## таблица проверок для AUDIT.md
	deckgen audit-catalog --markdown
bench:
	python scripts/benchmark.py
