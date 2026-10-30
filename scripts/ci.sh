#!/bin/sh
# Pipeline de CI (idéntico en GitHub Actions y en `docker compose --profile ci run --rm ci`).
set -eu
echo "== ruff check";   ruff check src tests
echo "== ruff format";  ruff format --check src tests
echo "== mypy";         mypy
echo "== pytest";       pytest -m "not network" "$@"
