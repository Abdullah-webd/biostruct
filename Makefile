# Biostruct developer commands.
#
# Everything runs through Docker, so the only prerequisites are Docker and a
# .env with BOLTZ_API_KEY and OPENAI_API_KEY. See docs/credentials.md.

.DEFAULT_GOAL := help
.PHONY: help up down logs test lint verify-boltz verify-sources verify-e2e reset status

help: ## Show this help
	@echo "Biostruct"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Start here:  make up  then open http://localhost:8000/docs"

up: ## Start MongoDB and the API, then show where to go
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
