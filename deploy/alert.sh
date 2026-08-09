#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
. "$ENV_FILE"
[ -n "${EVIDENCE_ALERT_URL:-}" ] || { echo "EVIDENCE_ALERT_URL is unset" >&2; exit 1; }
exec curl -fsS --max-time 20 -H 'content-type: application/json' \
  --data "{\"text\":\"Evidence collector health failure on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
  "$EVIDENCE_ALERT_URL"

