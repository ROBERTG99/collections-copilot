# collections-copilot — every target documented in README.md.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

export PATH := $(HOME)/.local/bin:$(HOME)/go/bin:$(PATH)

UV ?= uv
RUN := $(UV) run --frozen
PNPM ?= pnpm
COMPOSE ?= docker compose
TF_DIR := infra/terraform/envs/dev
DEPS := postgres redis elasticmq minio wiremock-twilio mailpit
PY_SRC := src apps third_party scripts tests data migrations

.PHONY: help bootstrap up down seed demo logs check-fast test test-unit test-integration \
	test-docker e2e eval eval-live bench lint fmt typecheck security verify verify-clean docs \
	clean gen-types record-fixtures tf-check tf-test dev-token db-shell deps-up deps-down \
	migrate images coverage observability

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

bootstrap: ## Install toolchains and dependencies, create .env from .env.example
	./scripts/install-tools.sh
	$(UV) sync --frozen
	$(PNPM) install --frozen-lockfile
	@test -f .env || cp .env.example .env

up: ## Start the full stack in Docker Compose with healthchecks
	./scripts/time_up.sh

down: ## Stop the stack (every profile)
	$(COMPOSE) --profile observability --profile jobs down

seed: ## Load the synthetic dataset (gen.v1, seed 4242)
	$(COMPOSE) run --rm seed

demo: ## Run the scripted demo scenario against the running stack, offline
	$(RUN) python scripts/demo.py

logs: ## Follow the logs of every service
	$(COMPOSE) logs -f --tail=100

check-fast: ## Lint, types and unit tests (< 60 s)
	$(RUN) ruff check $(PY_SRC)
	$(RUN) ruff format --check $(PY_SRC)
	$(RUN) lint-imports
	$(RUN) mypy src apps/dev-oidc/src apps/ledger-mcp/src apps/debtor-sim/src
	$(RUN) pytest tests/unit tests/harness -m "not integration and not docker" -q -x

deps-up: ## Start only the infrastructure dependencies used by host tests
	$(COMPOSE) up -d --wait $(DEPS)

deps-down:
	$(COMPOSE) stop $(DEPS)

test-unit:
	$(RUN) pytest tests/unit tests/harness -q

test-integration: deps-up
	$(RUN) pytest tests/integration tests/contract -q -m "not docker"

test: deps-up ## Unit, integration and contract tests on the host (with coverage)
	$(RUN) pytest tests/unit tests/harness tests/integration tests/contract -m "not docker and not perf" \
		--cov --cov-report=term:skip-covered --cov-report=xml -q
	$(RUN) python scripts/coverage_gate.py coverage.xml
	cd web && $(PNPM) run test:coverage

coverage:
	$(RUN) python scripts/coverage_gate.py coverage.xml

test-docker: ## The suites inside the tools image, then the stack tests (`-m docker`); the stack stays up
	./scripts/time_up.sh
	$(COMPOSE) -f compose.yaml -f compose.test.yaml --profile test run --rm --build tests
	$(RUN) pytest tests/integration -m docker -q

e2e: ## Playwright end-to-end tests (+ axe) against the running stack
	PNPM="$(PNPM)" ./scripts/e2e.sh

eval: ## Offline evals with thresholds; writes evals/REPORT.md
	$(RUN) python scripts/eval_report.py --mode offline

eval-live: ## Live evals with real models within LIVE_EVAL_BUDGET_USD
	$(RUN) python scripts/eval_live.py

bench: ## Performance tests with budgets (k6 + batch of 400)
	./scripts/bench.sh

lint: ## ruff, eslint, prettier, markdownlint, terraform fmt
	$(RUN) ruff check $(PY_SRC)
	$(RUN) ruff format --check $(PY_SRC)
	$(RUN) lint-imports
	cd web && $(PNPM) run lint && $(PNPM) run format:check
	$(PNPM) run lint:md
	terraform fmt -check -recursive infra/terraform

fmt: ## Apply formatters
	$(RUN) ruff check --fix $(PY_SRC)
	$(RUN) ruff format $(PY_SRC)
	cd web && $(PNPM) run format
	terraform fmt -recursive infra/terraform

typecheck: ## mypy --strict on src/ and apps/, tsc --noEmit
	$(RUN) mypy src apps/dev-oidc/src apps/ledger-mcp/src apps/debtor-sim/src
	cd web && $(PNPM) run typecheck

security: ## gitleaks, bandit, pip-audit, pnpm audit, trivy, hadolint, checkov, tflint, actionlint, licences
	./scripts/security.sh

tf-test: ## terraform test only (offline, mock_provider)
	./scripts/tf.sh test

tf-check: ## fmt, validate, test, tflint, checkov (offline)
	./scripts/tf.sh check

observability: ## Full stack plus Jaeger, Prometheus and Grafana (8391), with trace export on
	CC_OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 docker compose --profile observability up -d --wait --build

verify: ## Everything except eval-live; the single gate
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) security
	$(MAKE) tf-check
	$(MAKE) eval
	$(MAKE) bench

verify-clean: ## make verify in a fresh clone in a temporary folder
	./scripts/verify_clean.sh

docs: ## Regenerate diagrams, OpenAPI, frontend types and screenshots
	$(MAKE) gen-types
	$(RUN) python scripts/gen_docs.py
	cd web && $(PNPM) exec playwright test tests/e2e/screenshots.spec.ts

gen-types: ## Export OpenAPI and regenerate web/lib/api/schema.d.ts
	$(RUN) python scripts/export_openapi.py docs/openapi.json
	cd web && $(PNPM) exec openapi-typescript ../docs/openapi.json -o lib/api/schema.d.ts

record-fixtures: ## Record live LLM cassettes within budget
	$(RUN) python scripts/eval_live.py --record-fixtures

migrate:
	$(RUN) alembic upgrade head

dev-token: ## Print a dev agent JWT for the ledger through mcp-gateway
	@$(RUN) cc dev-token

db-shell: ## psql on the dev database
	psql postgresql://cc:cc@localhost:8320/cc

images:
	$(COMPOSE) build

clean: ## Remove containers, volumes, caches and build outputs
	-$(COMPOSE) -f compose.yaml -f compose.test.yaml --profile observability --profile jobs --profile test down -v --remove-orphans
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis htmlcov coverage.xml .coverage \
		web/.next web/coverage web/playwright-report web/test-results .evalgate
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
