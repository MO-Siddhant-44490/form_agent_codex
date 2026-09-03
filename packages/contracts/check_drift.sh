#!/usr/bin/env bash
# Fails if the committed JSON Schemas or generated TS are out of date with the
# Pydantic source of truth (AGENTS.md: CI fails on drift).
set -euo pipefail
cd "$(dirname "$0")/../.."

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp -R packages/contracts/schema "$tmp/schema.orig"
cp -R packages/contracts/ts/src/generated "$tmp/generated.orig"

uv run python packages/contracts/python/scripts/export_schemas.py >/dev/null
pnpm --filter @form-agent/contracts generate >/dev/null

diff -r "$tmp/schema.orig" packages/contracts/schema \
  || { echo "DRIFT: schema/ is stale; commit the regenerated schemas." >&2; exit 1; }
diff -r "$tmp/generated.orig" packages/contracts/ts/src/generated \
  || { echo "DRIFT: ts/src/generated is stale; commit the regenerated output." >&2; exit 1; }
echo "contracts: no drift"
