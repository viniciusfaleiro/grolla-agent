#!/usr/bin/env bash
# Sobe o stack do profile e2e, roda os cenários e derruba tudo, mesmo se algo falhar.
set -euo pipefail
cd "$(dirname "$0")/.."

compose=(docker compose --profile e2e --project-name grolla-e2e)
trap '"${compose[@]}" down --volumes --remove-orphans > /dev/null 2>&1' EXIT

"${compose[@]}" up --detach --build --wait --wait-timeout 180
uv run pytest e2e/test_scenarios.py -p no:cacheprovider "$@"
