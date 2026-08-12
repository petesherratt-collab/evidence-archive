#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
. "$ENV_FILE"
[ -n "${EVIDENCE_ALERT_URL:-}" ] || { echo "EVIDENCE_ALERT_URL is unset" >&2; exit 1; }
state="${EVIDENCE_HEALTH_STATE:?EVIDENCE_HEALTH_STATE is required}"
stamp="${EVIDENCE_ALERT_STAMP:?EVIDENCE_ALERT_STAMP is required}"
minimum="${EVIDENCE_ALERT_MIN_INTERVAL_SECONDS:-21600}"
now=$(date +%s)
if [ -f "$stamp" ]; then
  previous=$(stat -c %Y "$stamp")
  if [ $((now - previous)) -lt "$minimum" ]; then
    echo "Alert suppressed: previous successful delivery was less than ${minimum}s ago" >&2
    exit 0
  fi
fi
mkdir -p "$(dirname "$stamp")"
detail="health state unavailable"
[ ! -r "$state" ] || detail=$(tail -n 20 "$state")
payload=$(jq -cn --arg host "$(hostname)" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg detail "$detail" '{text:("Evidence collector health failure on " + $host + " at " + $at), detail:$detail}')
curl -fsS --max-time 20 -H 'content-type: application/json' \
  --data "$payload" "$EVIDENCE_ALERT_URL"
touch "$stamp"
