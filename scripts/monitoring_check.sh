#!/usr/bin/env bash
# After scripts/smoke_test.sh, against the stack with docker-compose.monitoring.yml: Prometheus
# scrapes the API (with its token) and the worker, the smoke test's jobs are in the worker's
# metrics, and no critical alert fires.
#
#   TESTMIND_PROMETHEUS_URL (default http://127.0.0.1:9090)
set -euo pipefail

PROMETHEUS="${TESTMIND_PROMETHEUS_URL:-http://127.0.0.1:9090}"

fail() { echo "MONITORING FAILED: $*" >&2; exit 1; }

# The value of an instant query's single result ("" when it has none).
query() {
  curl -fsS --get "$PROMETHEUS/api/v1/query" --data-urlencode "query=$1" | python3 -c '
import json, sys
result = json.load(sys.stdin)["data"]["result"]
print(result[0]["value"][1] if result else "")'
}

# Scrapes happen every 15 s: wait until both targets are up and the tasks have been scraped.
for _ in $(seq 1 30); do
  up=$(query 'sum(up{job=~"testmind-api|testmind-worker"})')
  tasks=$(query 'sum(testmind_tasks_total{outcome="succeeded"})')
  [[ "$up" == "2" && -n "$tasks" && "$tasks" != "0" ]] && break
  sleep 2
done
[[ "$up" == "2" ]] || fail "targets up: ${up:-none} of 2 (is the API token passed to Prometheus?)"
echo "→ both targets are scraped (the API with its token)"

[[ -n "$tasks" && "$tasks" != "0" ]] || fail "no succeeded task in the worker's metrics"
echo "  $tasks worker tasks succeeded"
runs=$(query 'sum(testmind_sandbox_runs_total)')
if [[ "${TESTMIND_EXPECT_SANDBOX:-0}" == "1" ]]; then
  [[ -n "$runs" && "$runs" != "0" ]] || fail "no sandbox run in the worker's metrics"
fi
echo "  ${runs:-0} sandbox runs"
[[ "$(query 'testmind_dependency_up{dependency="database"}')" == "1" ]] || fail "database down"
[[ "$(query 'testmind_dependency_up{dependency="redis"}')" == "1" ]] || fail "redis down"
echo "  the API reads PostgreSQL and Redis"
requests=$(query 'sum(testmind_http_requests_total{route=~"/api/.*"})')
[[ -n "$requests" && "$requests" != "0" ]] || fail "no API request counted"
echo "  $requests API requests counted"

critical=$(curl -fsS "$PROMETHEUS/api/v1/alerts" | python3 -c '
import json, sys
alerts = json.load(sys.stdin)["data"]["alerts"]
print(" ".join(a["labels"]["alertname"] for a in alerts
               if a["state"] == "firing" and a["labels"].get("severity") == "critical"))')
[[ -z "$critical" ]] || fail "critical alerts firing: $critical"
echo "  no critical alert firing"
echo "MONITORING OK"
