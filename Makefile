.PHONY: lint format-check format test

UV ?= uv
UV_CACHE_DIR ?= .cache/uv
UV_RUN = UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run

lint:
	$(UV_RUN) ruff check .

format-check:
	$(UV_RUN) ruff format . --check

format:
	ruff format .
	ruff check . --select I001 --fix
	ruff check . --select F401 --fix

test:
	$(UV_RUN) coverage run -m pytest -q
	$(UV_RUN) coverage report
