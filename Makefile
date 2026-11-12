# Biostruct developer commands.
#
# `make dev` runs the API directly on this machine and is the recommended way
# to use it: Docker runtimes that use a VM (Colima, Rancher, Podman machine)
# keep published ports inside that VM, so a containerised API is often
# unreachable from the host browser even though the container is healthy.
#
# Tests still run in Docker, because they need a MongoDB replica set and the
# container network reaches it by service name.
#
# Needs a .env with BOLTZ_API_KEY and OPENAI_API_KEY. See docs/credentials.md.

.DEFAULT_GOAL := help
.PHONY: help dev web web-dev up down logs test lint verify-boltz verify-sources verify-e2e verify-ui reset status

# Port 3000 is a popular default, so pick the first free one rather than
# failing or trampling whatever is already running there.
WEB_PORT ?= $(shell for p in 3000 3001 3002 3003 3004; do \
	if ! lsof -nP -iTCP:$$p -sTCP:LISTEN >/dev/null 2>&1; then echo $$p; break; fi; \
	done)

help: ## Show this help
	@echo "Biostruct"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Start here:  make dev   (API)   then   make web   (the interface)"

dev: ## Run the API natively on this Mac (recommended; no Docker networking)
	@# Colima and similar Docker runtimes keep published ports inside their VM,
	@# so a containerised API is often unreachable from the host browser.
	@# Running uvicorn directly avoids that entirely. Uses MONGODB_URI from
	@# .env, which points at Atlas, so no local database is needed.
	@test -d apps/api/.venv || (echo "No venv. Run: cd apps/api && uv venv --python 3.12 && uv pip install -e '.[dev]'" && exit 1)
	@echo "Starting the API on http://localhost:8000"
	@echo "Open http://localhost:8000/docs  (Ctrl-C to stop)"
	@echo ""
	cd apps/api && .venv/bin/uvicorn biostruct.main:app --reload --host 127.0.0.1 --port 8000

# Runs the production build rather than the dev server. It is the build that
# gets verified, it starts fast, and it does not depend on the hot-reload
# websocket, which some sandboxed or proxied environments block — and when that
# websocket fails, the page renders but never becomes interactive, which looks
# exactly like a broken app. Use web-dev when editing the frontend.
web: ## Run the web app (needs `make dev` running in another terminal)
	@test -d apps/web/node_modules || (cd apps/web && npm install)
	@if [ -z "$(WEB_PORT)" ]; then echo "No free port between 3000 and 3004."; exit 1; fi
	@if [ "$(WEB_PORT)" != "3000" ]; then \
		echo "Port 3000 is in use, so using $(WEB_PORT) instead."; \
		lsof -nP -iTCP:3000 -sTCP:LISTEN 2>/dev/null | tail -n +2 \
			| awk '{print "  (3000 is held by " $$1 ", pid " $$2 ")"}'; \
	fi
	@cd apps/web && npm run build
	@echo ""
	@echo "  Open http://localhost:$(WEB_PORT)"
	@echo ""
	cd apps/web && PORT=$(WEB_PORT) npm run start

web-dev: ## Run the web app with hot reload, for editing the frontend
	@test -d apps/web/node_modules || (cd apps/web && npm install)
	@echo "  Open http://localhost:$(WEB_PORT)  (hot reload)"
	cd apps/web && PORT=$(WEB_PORT) npm run dev

verify-ui: ## Prove the motion works and walk the whole UI, capturing screenshots
	cd apps/web && BASE_URL=http://127.0.0.1:$(WEB_PORT) npm run motion-check \
		&& BASE_URL=http://127.0.0.1:$(WEB_PORT) npm run walkthrough

up: ## Start MongoDB and the API in Docker (needs working port forwarding)
	docker compose --profile app up -d --wait --build
	@echo ""
	@echo "API      : http://localhost:8000"
	@echo "Try it   : http://localhost:8000/docs   (interactive, you can click through the whole workflow)"
	@echo "Health   : http://localhost:8000/readyz"
	@echo ""
	@docker compose exec -T api python -c "import urllib.request,json; \
		print('readyz:', json.dumps(json.load(urllib.request.urlopen('http://localhost:8000/readyz')), indent=2))" \
		2>/dev/null || true

down: ## Stop everything
	docker compose --profile app --profile test down

reset: ## Stop everything and delete the database
	docker compose --profile app --profile test down -v

status: ## Show what is running
	@docker compose ps

logs: ## Follow the API and reconciler logs
	docker compose logs -f api

test: ## Run the whole test suite
	docker compose up -d --wait mongo
	docker compose --profile test run --rm api-test

lint: ## Lint and type-check
	cd apps/api && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy

verify-boltz: ## Check the Boltz adapter against the live API (free on a test key)
	cd apps/api && .venv/bin/python scripts/verify_boltz.py

verify-sources: ## Check UniProt, RCSB and PubMed, and the residue mapping
	cd apps/api && .venv/bin/python scripts/verify_sources.py

verify-e2e: ## Run the whole workflow against the running API and real Boltz
	docker compose --profile app up -d --wait --build api
	docker compose run --rm --no-deps \
		-v ./apps/api/scripts:/s:ro \
		-e BOLTZ_API_KEY="$$(grep '^BOLTZ_API_KEY=' .env | cut -d= -f2-)" \
		--entrypoint python api-test /s/verify_e2e.py http://api:8000
