.PHONY: install test test-jsui lint run build smoke

install:
	python -m pip install -e '.[dev,providers,desktop]'

test:
	python -m pytest -q -m 'not live'

test-jsui:
	python -m pytest -q tests/test_jsui_*.py tests/test_job_snapshots.py tests/test_api_contracts.py tests/test_provider_preflight.py

lint:
	python -m ruff check codemerge tests --select F821

run:
	mergemonster

build:
	python -m build

smoke:
	python tools/import_guard.py
	python tools/smoke_imports.py
	python tools/smoke_server.py
