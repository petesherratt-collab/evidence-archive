#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
. "$ENV_FILE"
: "${EVIDENCE_DEADMAN_URL:?EVIDENCE_DEADMAN_URL is required}"
: "${EVIDENCE_DEADMAN_TOKEN_FILE:?EVIDENCE_DEADMAN_TOKEN_FILE is required}"
: "${EVIDENCE_COLLECTOR_INSTANCE_ID:?EVIDENCE_COLLECTOR_INSTANCE_ID is required}"
[ -r "$EVIDENCE_DEADMAN_TOKEN_FILE" ] || { echo "dead-man token unreadable" >&2; exit 1; }
event="${1:?event name is required}"
detail="${2:-}"
token=$(<"$EVIDENCE_DEADMAN_TOKEN_FILE")
payload=$(jq -cn --arg event "$event" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg instance "$EVIDENCE_COLLECTOR_INSTANCE_ID" --arg host "$(hostname)" \
  --arg detail "$detail" \
  '{event:$event,sent_at:$at,instance_id:$instance,hostname:$host,detail:$detail}')
printf 'header = "authorization: Bearer %s"\n' "$token" |
  curl -fsS --max-time 20 -X POST --config - \
    -H 'content-type: application/json' --data "$payload" "$EVIDENCE_DEADMAN_URL"
