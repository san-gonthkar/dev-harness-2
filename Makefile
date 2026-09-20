.PHONY: install lint typecheck test ci coverage mutation

install:
pip install -e ".[dev]"

lint:
ruff check src tests scripts
ruff format --check src tests scripts

typecheck:
mypy src

test:
pytest tests -q

coverage:
pytest tests -q --cov=dev_harness --cov-branch --cov-report=term-missing

ci: lint typecheck test coverage
python scripts/coverage_gate.py
