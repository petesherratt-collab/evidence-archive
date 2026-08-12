#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
. "$ENV_FILE"
state="${EVIDENCE_HEALTH_STATE:?EVIDENCE_HEALTH_STATE is required}"
mkdir -p "$(dirname "$state")"
tmp="${state}.tmp.$$"
if {
  "$EVIDENCE_ROOT/.venv/bin/python" "$EVIDENCE_REPO/deploy/health-check.py" \
    --archive "$EVIDENCE_ARCHIVE" --control "$EVIDENCE_CONTROL_CHECK" \
    --url "$EVIDENCE_CONTROL_URL" \
    --max-publisher-age "$EVIDENCE_CONTROL_MAX_PUBLISHER_AGE_SECONDS" \
    --max-poll-age "$EVIDENCE_CONTROL_MAX_POLL_AGE_SECONDS"
} >"$tmp" 2>&1; then
  mv "$tmp" "$state"
  exit 0
else
  rc=$?
  mv "$tmp" "$state"
  cat "$state" >&2
  exit "$rc"
fi
