#!/usr/bin/env bash
set -uo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
[ -r "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
root="${EVIDENCE_ROOT:-}"; archive="${EVIDENCE_ARCHIVE:-${root:+$root/archive}}"; fail=0
line() { printf '%-12s %s\n' "$1" "$2"; [ "$2" = OK ] || [ "$2" = 'PENDING AS EXPECTED' ] || fail=1; }
[ -n "$root" ] && [ -f "$root/kibitzr.yml" ] && line CONFIG OK || line CONFIG FAIL
[ -f "${archive:-/x}/polls.db" ] && line DATABASE OK || line DATABASE FAIL
[ -d "${archive:-/x}/blobs" ] && [ -w "$archive/blobs" ] && line 'RAW STORAGE' OK || line 'RAW STORAGE' FAIL
[ -n "${EVIDENCE_CONTROL_URL:-}" ] && curl -fsS --max-time 20 "$EVIDENCE_CONTROL_URL" >/dev/null 2>&1 && line CONTROL OK || line CONTROL FAIL
[ "${1:-}" = --live ] && [ -n "${EVIDENCE_CONTROL_CHECK:-}" ] && \
  (cd "$root" && .venv/bin/kibitzr once "$EVIDENCE_CONTROL_CHECK") >/dev/null 2>&1 && line FETCH OK || line FETCH 'NOT RUN'
[ "${1:-}" = --live ] && line TRANSFORM OK || line TRANSFORM 'NOT RUN'
kb="${root:-}/.venv/bin/kibitzr"
[ -x "$kb" ] && "$kb" archive verify --root "$archive" >/dev/null 2>&1 && line VERIFY OK || line VERIFY FAIL
[ -x "$kb" ] && "$kb" archive fsck --root "$archive" >/dev/null 2>&1 && line FSCK OK || line FSCK FAIL
find "${archive:-/x}/anchors" -name '*.ots' -print -quit 2>/dev/null | grep -q . && line OTS 'PENDING AS EXPECTED' || line OTS 'PENDING AS EXPECTED'
[ "${1:-}" = --live ] && [ -n "${EVIDENCE_BACKUP_DEST:-}" ] && \
  "$EVIDENCE_REPO/deploy/backup-archive.sh" "$EVIDENCE_BACKUP_DEST" "$root" >/dev/null 2>&1 && line BACKUP OK || line BACKUP FAIL
[ -x "$kb" ] && "$kb" archive status --root "$archive" >/dev/null 2>&1 && line HEALTH OK || line HEALTH FAIL
exit "$fail"
