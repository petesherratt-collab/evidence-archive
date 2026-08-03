#!/usr/bin/env bash
# Copy the archive somewhere else, and refuse to call it a backup until the
# copy has been checked.
#
# The archive is the only part of this project that exists in one place. The
# repository is on GitHub and the collector is a hundred lines of config; the
# poll log, the retained responses and the timestamp proofs are not recoverable
# from anywhere if this laptop's disk fails. So this script exists, and its
# entire value is in not lying about its result.
#
# Two failure modes it is built against, both of which produce a copy that
# looks fine:
#
#   A plain `cp` of polls.db while the collector is running. Journal mode is
#   `delete`, not WAL, so a copy taken mid-transaction captures a torn database
#   whose rollback journal is a separate file the copy did not include. It
#   mounts, it opens, and it fails to verify exactly when it is needed. The fix
#   is SQLite's own VACUUM INTO, which takes a read lock and writes a
#   consistent snapshot — no need to stop collection. The resulting file is
#   defragmented and byte-different from the original, which is fine: what is
#   preserved is the rows and their chains, not the file image.
#
#   A copy to removable media that fills up or is unplugged part-way. This is
#   the one `verify` cannot see: every chain recomputes from polls.db alone, so
#   a copy holding a perfect log and zero blobs reports all chains intact. That
#   is what `archive fsck` is for, and why this script runs both.
#
# The copy is assembled under a name announcing it is not finished, and is only
# renamed once verify and fsck have both passed. An interrupted run therefore
# leaves `.incomplete-*` on the drive rather than something mistakable for a
# good backup — the same tmp-then-rename discipline the blob store uses.
#
# Usage:  backup-archive.sh /media/peters/DRIVE [/path/to/run-root]
set -euo pipefail

DEST_PARENT="${1:-}"
# The run root holds archive/ and .venv/; this script lives in repo/deploy/
# inside it. Derived rather than hardcoded because moving collection to an
# always-on host is intended, and a path constant is a thing that gets missed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ROOT="${2:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

SOURCE="$RUN_ROOT/archive"
PYTHON="$RUN_ROOT/.venv/bin/python"
KIBITZR="$RUN_ROOT/.venv/bin/kibitzr"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGING="$DEST_PARENT/.incomplete-$STAMP"
FINAL="$DEST_PARENT/evidence-archive-$STAMP"

die() {
    echo "" >&2
    echo "########################################################" >&2
    echo "## BACKUP FAILED — no usable copy was written." >&2
    echo "## $*" >&2
    if [ -d "$STAGING" ]; then
        echo "## Partial copy left at: $STAGING" >&2
        echo "## It is deliberately named so it cannot be mistaken for one." >&2
    fi
    echo "########################################################" >&2
    exit 1
}

trap 'die "Interrupted or failed at line $LINENO."' ERR

[ -n "$DEST_PARENT" ] || die "No destination given. Usage: $0 DEST [RUN_ROOT]"
[ -d "$DEST_PARENT" ] || die "Destination '$DEST_PARENT' is not a directory."
[ -w "$DEST_PARENT" ] || die "Destination '$DEST_PARENT' is not writable."
[ -f "$SOURCE/polls.db" ] || die "No archive at '$SOURCE'."
[ -x "$PYTHON" ]  || die "No python at '$PYTHON'."
[ -x "$KIBITZR" ] || die "No kibitzr at '$KIBITZR'."

# Space, checked before starting rather than discovered half way. The archive
# compresses badly (blobs are already gzipped), so source size plus a fifth is
# a fair estimate of what this needs.
need_kb=$(( $(du -sk "$SOURCE" | cut -f1) * 12 / 10 ))
free_kb=$(df -Pk "$DEST_PARENT" | awk 'NR==2 {print $4}')
[ "$free_kb" -ge "$need_kb" ] || \
    die "Need ~${need_kb}KB, only ${free_kb}KB free on the destination."

# Back up something known-good. Copying an already-damaged archive and
# reporting success would be the same silent failure one step earlier.
echo "== Checking the source archive before copying it"
"$KIBITZR" archive verify --root "$SOURCE" || die "The SOURCE archive fails verify."
"$KIBITZR" archive fsck   --root "$SOURCE" || die "The SOURCE archive fails fsck."

echo ""
echo "== Copying to $STAGING"
mkdir -p "$STAGING"

# Consistent snapshot against a live writer. See the note at the top.
"$PYTHON" - "$SOURCE/polls.db" "$STAGING/polls.db" <<'PY'
import sqlite3
import sys

src, dest = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(src)
try:
    conn.execute("VACUUM INTO ?", (dest,))
finally:
    conn.close()
PY

# -a to preserve mtimes: when a proof was written is part of the record.
cp -a "$SOURCE/blobs" "$STAGING/"
# `if`, not `[ ... ] && cp`: under `set -e` the latter fails the whole run on
# an archive that has simply never been anchored.
if [ -d "$SOURCE/anchors" ]; then
    cp -a "$SOURCE/anchors" "$STAGING/"
fi

cat > "$STAGING/BACKUP.txt" <<EOF
Evidence archive backup
  taken     $(date -u +%Y-%m-%dT%H:%M:%SZ)
  from      $(hostname):$SOURCE
  by        $(basename "${BASH_SOURCE[0]}")

This copy was verified at the time it was written. To re-check it later,
against the same kibitzr-archive plugin:

  kibitzr archive verify --root /path/to/this/directory
  kibitzr archive fsck   --root /path/to/this/directory

verify recomputes the hash chains from polls.db. fsck checks the retained
responses in blobs/ and the proofs in anchors/, which no chain covers.
Both must pass. See deploy/VERIFYING.md in the repository for verification
that does not rely on this plugin at all.
EOF

# Before verifying, not after: reading back through the page cache would
# confirm what the kernel is holding rather than what reached the device.
echo ""
echo "== Flushing to the device"
sync -f "$STAGING/polls.db" 2>/dev/null || sync

echo ""
echo "== Verifying the copy"
"$KIBITZR" archive verify --root "$STAGING" || die "The COPY fails verify."
"$KIBITZR" archive fsck   --root "$STAGING" || die "The COPY fails fsck."

mv "$STAGING" "$FINAL"
sync -f "$FINAL/polls.db" 2>/dev/null || sync

trap - ERR
echo ""
echo "== Backup complete and checked: $FINAL"
echo "   $(du -sh "$FINAL" | cut -f1) — chains recomputed, blobs and proofs all present."
