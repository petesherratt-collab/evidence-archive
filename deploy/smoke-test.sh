#!/usr/bin/env bash
set -uo pipefail
ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
[ -r "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
root="${EVIDENCE_ROOT:-}"; archive="${EVIDENCE_ARCHIVE:-${root:+$root/archive}}"; fail=0
line() {
  printf '%-12s %s\n' "$1" "$2"
  [ "$2" = OK ] || [ "$2" = 'PENDING AS EXPECTED' ] || \
    [ "$2" = NOT_RUN ] || fail=1
}
kb="${root:-}/.venv/bin/kibitzr"; py="${root:-}/.venv/bin/python"

[ -x "$py" ] && "$py" -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); assert d.get("checks")' "$root/kibitzr.yml" >/dev/null 2>&1 && line CONFIG OK || line CONFIG FAIL
[ -f "${archive:-/x}/polls.db" ] && "$py" -c 'import sqlite3,sys; sqlite3.connect(sys.argv[1]).execute("pragma integrity_check").fetchone()' "$archive/polls.db" >/dev/null 2>&1 && line DATABASE OK || line DATABASE FAIL
probe="${archive:-/x}/.smoke-write-$$"; (umask 077; : > "$probe") 2>/dev/null && [ -f "$probe" ] && line 'RAW STORAGE' OK || line 'RAW STORAGE' FAIL; [ ! -e "$probe" ] || rm -f "$probe"

before=""
if [ "${1:-}" = --live ] && [ -n "${EVIDENCE_CONTROL_CHECK:-}" ]; then
  before="$($py -c 'from kibitzr_archive.store import ArchiveStore; import sys; s=ArchiveStore(sys.argv[1]); print(len(s.observations(sys.argv[2], False)))' "$archive" "$EVIDENCE_CONTROL_CHECK")"
  (cd "$root" && .venv/bin/kibitzr once "$EVIDENCE_CONTROL_CHECK") >/dev/null 2>&1
  read -r after latest_ok < <($py -c 'from kibitzr_archive.store import ArchiveStore; import sys; s=ArchiveStore(sys.argv[1]); rows=s.observations(sys.argv[2], False); print(len(rows), rows[-1]["ok"])' "$archive" "$EVIDENCE_CONTROL_CHECK")
  [ "$after" -eq $((before + 1)) ] && [ "$latest_ok" -eq 1 ] && line FETCH OK || line FETCH FAIL
  "$py" - "$archive" "$EVIDENCE_CONTROL_CHECK" <<'PY' >/dev/null 2>&1
import sys
from kibitzr_archive.store import ArchiveStore
s=ArchiveStore(sys.argv[1]); polls=s.observations(sys.argv[2], False)
with s._connect() as c:
    assert c.execute("select 1 from normalisation where poll_id=?", (polls[-1]["id"],)).fetchone()
PY
  [ "$?" -eq 0 ] && line TRANSFORM OK || line TRANSFORM FAIL
else
  line FETCH NOT_RUN
  line TRANSFORM NOT_RUN
fi

"$kb" archive verify --root "$archive" >/dev/null 2>&1 && line VERIFY OK || line VERIFY FAIL
"$kb" archive fsck --strict --root "$archive" >/dev/null 2>&1 && line FSCK OK || line FSCK FAIL
anchors="$($kb archive anchors --root "$archive" 2>&1)"
if grep -q ' complete ' <<<"$anchors"; then line OTS OK
elif grep -q ' pending ' <<<"$anchors"; then line OTS 'PENDING AS EXPECTED'
else line OTS FAIL; fi

if [ "${1:-}" = --live ] && [ -n "${EVIDENCE_BACKUP_DEST:-}" ]; then
  "$EVIDENCE_REPO/deploy/backup-archive.sh" "$EVIDENCE_BACKUP_DEST" "$root" >/dev/null 2>&1 && line BACKUP OK || line BACKUP FAIL
else line BACKUP NOT_RUN; fi

"$EVIDENCE_REPO/deploy/health-check.sh" >/dev/null 2>&1 && line HEALTH OK || line HEALTH FAIL
if [ "${1:-}" = --live ] && [ -n "${EVIDENCE_ALERT_URL:-}" ]; then
  "$EVIDENCE_REPO/deploy/alert.sh" >/dev/null 2>&1 && line ALERT OK || line ALERT FAIL
else line ALERT NOT_RUN; fi
exit "$fail"
