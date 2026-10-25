# Interface única do harness. `make check` precisa estar verde antes de qualquer commit ou push.
SHELL := /bin/bash
RUN := uv run
GITLEAKS_IMAGE := zricethezav/gitleaks:v8.30.1

.PHONY: setup fmt check format-check lint types arch vocabulary test coverage audit secrets spec e2e

setup: ## ativa os git hooks e instala as dependências
	git config core.hooksPath hooks
	chmod +x hooks/*
	uv sync

fmt: ## formata e aplica as correções automáticas do lint
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

check: format-check lint types arch vocabulary coverage audit secrets spec ## todos os gates rápidos
	@echo "make check: todos os gates verdes"

format-check:
	$(RUN) ruff format --check .

lint:
	$(RUN) ruff check .

types:
	$(RUN) pyright

arch:
	$(RUN) lint-imports

vocabulary:
	$(RUN) python scripts/check_vocabulary.py

test: ## unitários (sem rede) + integração (testcontainers), com cobertura
	$(RUN) pytest --cov --cov-report=json:coverage.json --cov-report=term-missing:skip-covered

coverage: test
	$(RUN) python scripts/coverage_floors.py

audit:
	@mkdir -p .cache
	uv export --frozen --all-packages --all-groups --no-emit-workspace \
		--output-file .cache/requirements-audit.txt > /dev/null
	$(RUN) pip-audit --strict --disable-pip --requirement .cache/requirements-audit.txt

secrets:
	@if command -v gitleaks > /dev/null; then \
		gitleaks dir --no-banner --redact --config .gitleaks.toml . ; \
	else \
		docker run --rm -v "$(CURDIR):/repo" $(GITLEAKS_IMAGE) \
			dir --no-banner --redact --config /repo/.gitleaks.toml /repo ; \
	fi

spec:
	openspec validate --all --strict --no-interactive

e2e: ## sobe o stack com os fakes, roda os cenários e derruba tudo
	bash scripts/e2e.sh
