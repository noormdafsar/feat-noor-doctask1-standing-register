COMPOSE ?= docker compose

.PHONY: up down demo test record seed logs psql clean fmt

## The one documented command: brings up db + api + watcher + web, seeds the corpus.
up:
	$(COMPOSE) up --build

## Full end-to-end on the demo family, printed to the terminal. Requires `make up` running.
demo:
	$(COMPOSE) exec -T api python -m app.cli demo

## The behaviour suite. Runs with GEMINI_API_KEY deliberately unset, on its own
## database so it can truncate freely without fighting the running watcher.
TEST_DB ?= postgresql+psycopg://sr:sr@db:5432/standing_register_test
test:
	$(COMPOSE) run --rm \
		-e GEMINI_API_KEY= \
		-e MODEL_BACKEND=fixture \
		-e DATABASE_URL=$(TEST_DB) \
		api pytest -q

## Record cassettes against the live Gemini API (needs GEMINI_API_KEY in the environment).
record:
	$(COMPOSE) run --rm -e MODEL_BACKEND=record api python -m app.cli record

## Populate the running system so every screen has real content to look at.
showcase:
	$(COMPOSE) exec -T api python scripts/showcase.py

## The MCP server on stdio, for a coding agent to attach to. See README "The machine face".
mcp:
	$(COMPOSE) run --rm -T api python -m app.mcp_server.server

seed:
	$(COMPOSE) exec -T api python -m app.cli seed

logs:
	$(COMPOSE) logs -f api watcher

psql:
	$(COMPOSE) exec db psql -U sr -d standing_register

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v
