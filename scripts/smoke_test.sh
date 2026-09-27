#!/usr/bin/env bash
# End-to-end smoke test against a running stack (`make up` first).
# Registers the example project, queues a run and waits until the worker finishes it.
set -euo pipefail

API="${TESTMIND_API_URL:-http://127.0.0.1:8000}"
TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-60}"
NAME="smoke-$(date +%s)-$RANDOM"

fail() { echo "SMOKE FAILED: $*" >&2; exit 1; }
json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

wait_for_run() {
  local deadline=$((SECONDS + TIMEOUT_SECONDS)) status=""
  while (( SECONDS < deadline )); do
    status=$(curl -fsS "$API/api/v1/runs/$1" | json "['status']")
    case "$status" in
      succeeded) return 0 ;;
      failed|cancelled) fail "run $1 ended as $status" ;;
      *) sleep 1 ;;
    esac
  done
  fail "run $1 did not finish within ${TIMEOUT_SECONDS}s"
}

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
wait_for_run "$run_id"
echo "  run succeeded"

echo "→ repository analysis"
analysis=$(curl -fsS "$API/api/v1/runs/$run_id/analysis")
echo "$analysis" | json "['primary_language']" | grep -qx Python || fail "primary language"
echo "$analysis" | json "[\"test_frameworks\"][0][\"name\"]" | grep -qx pytest \
  || fail "pytest not detected"
echo "$analysis" | json "['test_files']" | grep -q "tests/test_pricing.py" || fail "test files"
tests_total=$(curl -fsS "$API/api/v1/runs/$run_id/analysis/files?is_test=true" | json "['total']")
[[ "$tests_total" == "1" ]] || fail "expected 1 test file, got $tests_total"
echo "  python project, pytest detected, 1 test file"

echo "→ code analysis (clean project)"
code=$(curl -fsS "$API/api/v1/runs/$run_id/code")
echo "$code" | json "['metrics']['test_modules_skipped']" | grep -qx 1 || fail "test modules skipped"
echo "$code" | json "['metrics']['findings_by_severity']" | grep -qx "{}" || fail "unexpected findings"
echo "  no findings, test module excluded"

echo "→ code analysis (intentionally flawed project)"
buggy_id=$(curl -fsS -X POST "$API/api/v1/projects" -H 'Content-Type: application/json' \
  -d "{\"name\": \"$NAME-buggy\", \"source_location\": \"buggy_inventory\"}" | json "['id']")
buggy_run=$(curl -fsS -X POST "$API/api/v1/projects/$buggy_id/runs" | json "['id']")
wait_for_run "$buggy_run"
critical=$(curl -fsS "$API/api/v1/runs/$buggy_run/code/findings?min_severity=critical")
echo "$critical" | json "['items'][0]['rule_id']" | grep -qx TM010 || fail "eval() not reported"
total=$(curl -fsS "$API/api/v1/runs/$buggy_run/code/findings" | json "['total']")
[[ "$total" == "17" ]] || fail "expected 17 findings in buggy_inventory, got $total"
echo "  17 findings, eval() reported as critical"

echo "→ AI test generation"
generation_status=$(curl -s -o /tmp/testmind-smoke-generation.json -w '%{http_code}' \
  -X POST "$API/api/v1/runs/$run_id/test-generations")
case "$generation_status" in
  202) echo "  queued (LLM configured)" ;;
  503) grep -q llm_not_configured /tmp/testmind-smoke-generation.json \
         || fail "unexpected 503 body"
       echo "  no LLM configured: honest 503 llm_not_configured" ;;
  *) fail "unexpected status $generation_status for test generation" ;;
esac
rm -f /tmp/testmind-smoke-generation.json

echo "→ test execution"
execution_id=$(curl -fsS -X POST "$API/api/v1/runs/$run_id/test-executions" | json "['id']")
deadline=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  execution=$(curl -fsS "$API/api/v1/test-executions/$execution_id")
  execution_status=$(echo "$execution" | json "['status']")
  [[ "$execution_status" == "succeeded" || "$execution_status" == "failed" ]] && break
  sleep 1
done
[[ "$execution_status" == "succeeded" ]] || fail "test execution ended as $execution_status"
outcome=$(echo "$execution" | json "['outcome']")
coverage_status=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/v1/test-executions/$execution_id/coverage")
failures_status=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/v1/test-executions/$execution_id/failures")
case "$outcome" in
  passed)
    [[ "$coverage_status" == "200" ]] || fail "no coverage report for a passed execution"
    [[ "$failures_status" == "200" ]] || fail "no failure report for a finished execution"
    percent=$(echo "$execution" | json "['summary']['coverage_percent']")
    echo "  project tests passed in the sandbox, coverage ${percent}%"
    ;;
  sandbox_unavailable)
    [[ "$coverage_status" == "404" ]] || fail "coverage reported without a sandbox run"
    [[ "$failures_status" == "404" ]] || fail "failure report without a sandbox run"
    echo "  no Docker access for the containerised worker: honest sandbox_unavailable, no coverage or failure report"
    ;;
  *) fail "unexpected test execution outcome: $outcome" ;;
esac

echo "→ mutation testing"
mutation_id=$(curl -fsS -X POST "$API/api/v1/runs/$run_id/mutation-runs" \
  -H 'Content-Type: application/json' -d '{"max_mutants": 10}' | json "['id']")
deadline=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  mutation=$(curl -fsS "$API/api/v1/mutation-runs/$mutation_id")
  mutation_status=$(echo "$mutation" | json "['status']")
  [[ "$mutation_status" == "succeeded" || "$mutation_status" == "failed" ]] && break
  sleep 1
done
[[ "$mutation_status" == "succeeded" ]] || fail "mutation run ended as $mutation_status"
baseline=$(echo "$mutation" | json "['summary']['baseline_outcome']")
case "$baseline" in
  passed) echo "  mutation score $(echo "$mutation" | json "['score']")" ;;
  sandbox_unavailable) echo "  no Docker access for the containerised worker: honest sandbox_unavailable, no score" ;;
  *) fail "unexpected mutation baseline outcome: $baseline" ;;
esac

echo "→ regression analysis"
regression_status=$(curl -s -o /tmp/testmind-regression.json -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -d '{"base": "HEAD~1"}' \
  "$API/api/v1/projects/$project_id/regressions")
[[ "$regression_status" == "409" ]] || fail "expected 409 for a directory without git history, got $regression_status"
grep -q not_a_git_repository /tmp/testmind-regression.json || fail "unexpected regression error body"
echo "  example directories have no git history: honest 409 not_a_git_repository"

echo "→ clean up"
curl -fsS -X DELETE "$API/api/v1/projects/$project_id" -o /dev/null
curl -fsS -X DELETE "$API/api/v1/projects/$buggy_id" -o /dev/null
echo "SMOKE OK"
