#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
. "$ENV_FILE"
exec "$EVIDENCE_REPO/deploy/send-deadman-event.sh" collector-heartbeat
