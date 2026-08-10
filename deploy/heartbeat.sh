#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
. "$ENV_FILE"
: "${EVIDENCE_HEARTBEAT_URL:?EVIDENCE_HEARTBEAT_URL is required}"
: "${EVIDENCE_HEARTBEAT_TOKEN_FILE:?EVIDENCE_HEARTBEAT_TOKEN_FILE is required}"
: "${EVIDENCE_COLLECTOR_INSTANCE_ID:?EVIDENCE_COLLECTOR_INSTANCE_ID is required}"
[ -r "$EVIDENCE_HEARTBEAT_TOKEN_FILE" ] || { echo "heartbeat token unreadable" >&2; exit 1; }
token=$(<"$EVIDENCE_HEARTBEAT_TOKEN_FILE")
payload=$(jq -cn --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg instance "$EVIDENCE_COLLECTOR_INSTANCE_ID" \
  '{event_type:"collector-heartbeat",client_payload:{sent_at:$at,instance_id:$instance}}')
printf 'header = "authorization: Bearer %s"\n' "$token" |
  curl -fsS --max-time 20 -X POST --config - \
    -H 'accept: application/vnd.github+json' \
    -H 'x-github-api-version: 2022-11-28' \
    --data "$payload" "$EVIDENCE_HEARTBEAT_URL"
