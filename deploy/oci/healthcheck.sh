#!/usr/bin/env bash
# Quick health check after install / update, and for monitoring (exit 0 = healthy).
#   bash deploy/oci/healthcheck.sh [base-url]      default http://127.0.0.1
set -uo pipefail
BASE=${1:-http://127.0.0.1}
ok=1
check() { if eval "$2" &>/dev/null; then printf '  ok    %s\n' "$1"; else printf '  FAIL  %s\n' "$1"; ok=0; fi; }
echo "Kiswa-Zaibus health ($BASE)"
check "kz-api service running"        "systemctl is-active --quiet kz-api"
check "tick timer active"             "systemctl is-active --quiet kz-tick.timer"
check "nginx running"                 "systemctl is-active --quiet nginx"
check "kz-docs (Node) running"        "systemctl is-active --quiet kz-docs"
check "kz-docs answers on 8095"       "curl -fsS -m 5 http://127.0.0.1:8095/health | grep -q kz-docs"
for _ in 1 2 3 4 5; do curl -fsS -m 5 http://127.0.0.1:5000/api/health &>/dev/null && break; sleep 2; done
check "API answers on 127.0.0.1:5000" "curl -fsS -m 5 http://127.0.0.1:5000/api/health"
check "API database reachable"        "curl -fsS -m 5 http://127.0.0.1:5000/api/health | grep -q '\"database\": *\"ok\"'"
check "Nginx serves the app shell"    "curl -fsS -m 5 -H 'Host: localhost' $BASE/ | grep -q '<div id=\"root\">'"
check "Nginx proxies /api"            "curl -fsS -m 5 $BASE/api/health"
check "Service worker present"        "curl -fsS -m 5 -o /dev/null $BASE/sw.js"
if [[ $ok -eq 1 ]]; then echo "healthy"; else echo "unhealthy — see docs/TROUBLESHOOTING.md (section: Deployment)"; exit 1; fi
