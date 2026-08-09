#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
. "$ENV_FILE"
kb="$EVIDENCE_ROOT/.venv/bin/kibitzr"
"$kb" archive verify --root "$EVIDENCE_ARCHIVE" >/dev/null
"$kb" archive fsck --root "$EVIDENCE_ARCHIVE" >/dev/null
exec "$EVIDENCE_ROOT/.venv/bin/python" "$EVIDENCE_REPO/deploy/health-check.py" \
  --archive "$EVIDENCE_ARCHIVE" --control "$EVIDENCE_CONTROL_CHECK" \
  --url "$EVIDENCE_CONTROL_URL" \
  --max-publisher-age "$EVIDENCE_CONTROL_MAX_PUBLISHER_AGE_SECONDS" \
  --max-poll-age "$EVIDENCE_CONTROL_MAX_POLL_AGE_SECONDS"

