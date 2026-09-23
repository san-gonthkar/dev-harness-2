.PHONY: install lint typecheck test test-smoke test-full test-nightly coverage ci mutation

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests scripts
	ruff format --check src tests scripts

typecheck:
	mypy src

# FAST lane - regular/daily runs. Excludes NIGHTLY markers (timing/slow/e2e),
# coverage-padding *gaps* files, spikes, and meta-tests. See scripts/test_lane.*
test:
	python -m pytest tests -q -m "not timing and not slow and not e2e" --ignore-glob="**/*gaps*.py" --ignore=tests/spikes --ignore=tests/tooling --timeout=120

test-smoke: test

# FULL suite - run manually / on demand (phase gates, pre-commit).
test-full:
	python -m pytest tests -q

# NIGHTLY tier only (timing/slow/e2e).
test-nightly:
	python -m pytest tests -q -m "timing or slow or e2e"

# Full suite with branch coverage (needs every test for the coverage gate).
coverage:
	python -m pytest tests -q --cov=dev_harness --cov-branch --cov-report=term-missing

# PR gate: lint + types + full coverage + coverage gate (one full-suite run).
ci: lint typecheck coverage
	python scripts/coverage_gate.py

mutation:
	python scripts/mutation_gate.py --dry-run