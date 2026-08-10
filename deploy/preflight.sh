#!/usr/bin/env bash
set -uo pipefail

ENV_FILE="${EVIDENCE_ENV_FILE:-$HOME/.config/evidence-archive/environment}"
[ -r "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
ROOT="${EVIDENCE_ROOT:-}"
REPO="${EVIDENCE_REPO:-}"
ARCHIVE="${EVIDENCE_ARCHIVE:-${ROOT:+$ROOT/archive}}"
fail=0
ok() { printf '%-14s OK%s\n' "$1" "${2:+ - $2}"; }
bad() { printf '%-14s FAIL - %s\n' "$1" "$2"; fail=1; }
skip() { printf '%-14s SKIP - %s\n' "$1" "$2"; }
need() { command -v "$1" >/dev/null 2>&1 && ok "$1" "$(command -v "$1")" || bad "$1" "not installed"; }

[ -n "$ROOT" ] || bad CONFIG "EVIDENCE_ROOT is unset"
[ -n "$REPO" ] || bad CONFIG "EVIDENCE_REPO is unset"
[ -n "$ROOT" ] && [ -d "$ROOT" ] && [ -w "$ROOT" ] && ok ROOT "$ROOT" || bad ROOT "missing or not writable"
[ -n "$REPO" ] && [ -d "$REPO/.git" ] && ok REPOSITORY "$REPO" || bad REPOSITORY "not a git checkout"
need git
need jq
need systemd-analyze

if [ -x "${ROOT:-/nonexistent}/.venv/bin/python" ]; then
  py="$ROOT/.venv/bin/python"; kb="$ROOT/.venv/bin/kibitzr"
  ok PYTHON "$($py --version 2>&1)"
  [ -x "$kb" ] && ok KIBITZR "$($kb version 2>&1 | tail -1)" || bad KIBITZR "missing from venv"
  "$py" -c 'import kibitzr_archive; print(kibitzr_archive.__version__)' >/dev/null 2>&1 && ok PLUGIN || bad PLUGIN "not importable"
else
  bad PYTHON "deployment venv missing"
fi

if [ -f "${ROOT:-/nonexistent}/kibitzr.yml" ] && [ -x "${ROOT:-}/.venv/bin/python" ]; then
  "$ROOT/.venv/bin/python" - "$ROOT/kibitzr.yml" <<'PY' >/dev/null 2>&1
import sys, yaml
data = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
assert isinstance(data, dict) and isinstance(data.get("checks"), list) and data["checks"]
for check in data["checks"]:
    assert isinstance(check, dict) and check.get("name") and check.get("url")
PY
  [ "$?" -eq 0 ] && ok CONFIG || bad CONFIG "YAML or required check fields invalid"
else bad CONFIG "kibitzr.yml or deployment Python missing from run root"; fi
if [ -n "$ROOT" ] && [ -d "$ROOT" ]; then
  free_kb=$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')
  [ "${free_kb:-0}" -ge "${EVIDENCE_MIN_FREE_KB:-1048576}" ] && ok DISK "${free_kb} KiB free" || bad DISK "less than required free space"
fi
getent hosts example.org >/dev/null 2>&1 && ok DNS || bad DNS "resolution failed"
command -v curl >/dev/null && curl -fsSI --max-time 15 https://example.org/ >/dev/null 2>&1 && ok HTTPS || bad HTTPS "outbound HTTPS failed"

if [ -n "${EVIDENCE_CONTROL_URL:-}" ]; then
  curl -fsS --max-time 20 "$EVIDENCE_CONTROL_URL" >/dev/null 2>&1 && ok CONTROL || bad CONTROL "configured target unreachable"
else
  bad CONTROL "EVIDENCE_CONTROL_URL is unset"
fi

ots="${ROOT:-}/.venv-anchor/bin/ots"
[ -x "$ots" ] && "$ots" --version >/dev/null 2>&1 && ok OTS "$($ots --version 2>&1)" || bad OTS "client missing or unusable"
timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -qx yes && ok CLOCK || bad CLOCK "NTP not confirmed"

if [ -n "${EVIDENCE_BACKUP_DEST:-}" ]; then
  case "$EVIDENCE_BACKUP_DEST" in
    *:*) rclone_bin="${RCLONE:-rclone}"; "$rclone_bin" lsf "${EVIDENCE_BACKUP_DEST%%/*}" --max-depth 1 >/dev/null 2>&1 && ok BACKUP || bad BACKUP "remote unreachable" ;;
    *) [ -d "$EVIDENCE_BACKUP_DEST" ] && [ -w "$EVIDENCE_BACKUP_DEST" ] && ok BACKUP || bad BACKUP "local destination unavailable" ;;
  esac
else bad BACKUP "EVIDENCE_BACKUP_DEST is unset"; fi

if [ -n "${BACKUP_STAGE_DIR:-}" ] && [ -d "$BACKUP_STAGE_DIR" ] && [ -w "$BACKUP_STAGE_DIR" ]; then
  stage_fs=$(findmnt -n -o FSTYPE --target "$BACKUP_STAGE_DIR" 2>/dev/null || true)
  stage_free=$(df -Pk "$BACKUP_STAGE_DIR" | awk 'NR==2 {print $4}')
  archive_kb=$(du -sk "$ARCHIVE" 2>/dev/null | awk '{print $1}')
  archive_need=$(( ${archive_kb:-1} * 12 / 10 ))
  [ "$stage_fs" != tmpfs ] && [ "$stage_fs" != ramfs ] && \
    [ "${stage_free:-0}" -ge "${archive_need:-1}" ] && \
    ok BACKUP_STAGE "$BACKUP_STAGE_DIR ($stage_fs; ${stage_free} KiB free)" || \
    bad BACKUP_STAGE "must be persistent and hold at least 120% of the archive"
else bad BACKUP_STAGE "explicit writable BACKUP_STAGE_DIR is required"; fi

linger=$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)
[ "$linger" = yes ] && ok LINGER || bad LINGER "run loginctl enable-linger $USER"
[ -n "${EVIDENCE_ALERT_URL:-}" ] && ok ALERT "configured; delivery requires smoke test" || bad ALERT "EVIDENCE_ALERT_URL is unset"
[ -n "${EVIDENCE_COLLECTOR_INSTANCE_ID:-}" ] && ok INSTANCE "$EVIDENCE_COLLECTOR_INSTANCE_ID" || bad INSTANCE "stable EVIDENCE_COLLECTOR_INSTANCE_ID is unset"
if [ -n "${EVIDENCE_HEARTBEAT_URL:-}" ] && [ -r "${EVIDENCE_HEARTBEAT_TOKEN_FILE:-}" ]; then
  token_mode=$(stat -c %a "$EVIDENCE_HEARTBEAT_TOKEN_FILE")
  [ "$token_mode" = 600 ] && ok HEARTBEAT "configured; absence alarm still requires live test" || bad HEARTBEAT "token file mode must be 600"
else bad HEARTBEAT "URL or readable token file is missing"; fi

pgrep -af 'kibitzr (run|once)' >/dev/null 2>&1 && bad COLLECTOR "another collector appears to be running" || ok COLLECTOR "none detected"
if [ -n "$REPO" ]; then
  systemd-analyze verify "$REPO"/deploy/*.service "$REPO"/deploy/*.timer >/dev/null 2>&1 && ok SYSTEMD || bad SYSTEMD "unit verification failed"
fi
if [ -f "${ARCHIVE:-/nonexistent}/polls.db" ] && [ -x "${kb:-/nonexistent}" ]; then
  "$kb" archive verify --root "$ARCHIVE" >/dev/null && ok VERIFY || bad VERIFY "archive verify failed"
  "$kb" archive fsck --strict --root "$ARCHIVE" >/dev/null && ok FSCK || bad FSCK "archive fsck found damage or suspect state"
  "$REPO/deploy/health-check.sh" >/dev/null && ok HEALTH || bad HEALTH "publisher/collector freshness check failed"
else skip ARCHIVE "no existing archive"; fi
exit "$fail"
