#!/usr/bin/env bash
# End-to-end smoke test against a running stack (`make up` first).
# Registers the example project, queues a run and waits until the worker finishes it.
set -euo pipefail

API="${TESTMIND_API_URL:-http://127.0.0.1:8000}"
TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-60}"
NAME="smoke-$(date +%s)-$RANDOM"

fail() { echo "SMOKE FAILED: $*" >&2; exit 1; }
json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

echo "→ readiness"
curl -fsS "$API/ready" | tee /dev/stderr | grep -q '"status":"ready"' || fail "API not ready"
echo

echo "→ create project"
project_id=$(curl -fsS -X POST "$API/api/v1/projects" \
  -H 'Content-Type: application/json' \
  -d "{\"name\": \"$NAME\", \"source_location\": \"discount_service\"}" | json "['id']")
echo "  project $project_id"

echo "→ path traversal is rejected"
status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/v1/projects" \
  -H 'Content-Type: application/json' -d '{"name": "evil", "source_location": "../../etc"}')
[[ "$status" == "422" ]] || fail "expected 422 for traversal, got $status"

echo "→ queue run"
run_id=$(curl -fsS -X POST "$API/api/v1/projects/$project_id/runs" | json "['id']")
echo "  run $run_id"

echo "→ wait for worker"
deadline=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  run_status=$(curl -fsS "$API/api/v1/runs/$run_id" | json "['status']")
  case "$run_status" in
    succeeded) echo "  run succeeded"; break ;;
    failed|cancelled) fail "run ended as $run_status" ;;
    *) sleep 1 ;;
  esac
done
[[ "${run_status:-}" == "succeeded" ]] || fail "run did not finish within ${TIMEOUT_SECONDS}s"

echo "→ clean up"
curl -fsS -X DELETE "$API/api/v1/projects/$project_id" -o /dev/null
echo "SMOKE OK"
