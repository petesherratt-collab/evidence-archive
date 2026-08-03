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
# The destination may be a local directory or an rclone remote written
# `remote:bucket/path`. A remote is the one that runs without a human present,
# and it is off-site, which matters because what kills an archive is usually
# not disk failure but loss of the machine — theft, fire, or simply replacing
# the laptop.
#
# For a remote the copy is still staged and checked locally first, then
# uploaded, then compared by hash with `rclone check`. Verifying over a mounted
# remote instead would check through a network cache layer, which is the same
# error as verifying before `sync`.
#
# The remote cannot use the rename trick: a backup key should not hold delete
# permission, and a rename is a copy plus a delete. So the completion marker is
# a file instead — everything is uploaded, `rclone check` compares hashes, and
# only then is BACKUP-COMPLETE.txt written. A remote directory without that
# file is a known-incomplete upload. It is belt-and-braces regardless, because
# a partial upload is exactly what `fsck` detects on restore.
#
# Storage does not have to be trusted. Transfer corruption is caught by hash,
# and anything else — including a polls.db swapped for an older one — is caught
# by `fsck` on restore, which is what the chains and anchors were built for.
#
# Usage:  backup-archive.sh /media/peters/DRIVE   [/path/to/run-root]
#         backup-archive.sh b2:bucket/evidence    [/path/to/run-root]
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
RCLONE="${RCLONE:-$HOME/.local/bin/rclone}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# An rclone remote is `name:path`, with the colon before any slash. A local
# absolute path can never match, and a relative one with a colon in it would
# be a strange thing to hand a backup script.
case "$DEST_PARENT" in
    /*|.*|"") REMOTE="" ;;
    *:*)      REMOTE="yes" ;;
    *)        REMOTE="" ;;
esac

# Where the copy is assembled and checked. For a local destination that is the
# destination itself; for a remote it is scratch space on this machine, because
# verification has to happen against real local files.
if [ -n "$REMOTE" ]; then
    STAGE_PARENT="${BACKUP_STAGE_DIR:-${TMPDIR:-/tmp}}"
else
    STAGE_PARENT="$DEST_PARENT"
fi
STAGING="$STAGE_PARENT/.incomplete-$STAMP"
FINAL="$STAGE_PARENT/evidence-archive-$STAMP"

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
[ -f "$SOURCE/polls.db" ] || die "No archive at '$SOURCE'."
[ -x "$PYTHON" ]  || die "No python at '$PYTHON'."
[ -x "$KIBITZR" ] || die "No kibitzr at '$KIBITZR'."

if [ -n "$REMOTE" ]; then
    [ -x "$RCLONE" ] || die "No rclone at '$RCLONE'. Set \$RCLONE, or install it."
    remote_name="${DEST_PARENT%%:*}"
    "$RCLONE" listremotes 2>/dev/null | grep -qx "$remote_name:" || \
        die "rclone has no remote called '$remote_name:'. See deploy/README.md."
    # Reachability and credentials, before spending time on a copy that would
    # be thrown away. Also what catches an expired application key.
    #
    # Probe the bucket, not the remote's root and not the full destination
    # path. Not the root, because a key scoped to one bucket may not be
    # allowed to enumerate buckets at all; not the full path, because on the
    # first run the directory under it does not exist yet and must not be
    # treated as a failure.
    probe_rest="${DEST_PARENT#*:}"
    probe_lead=""
    case "$probe_rest" in /*) probe_lead="/"; probe_rest="${probe_rest#/}" ;; esac
    "$RCLONE" lsf "$remote_name:$probe_lead${probe_rest%%/*}" --max-depth 1 \
        >/dev/null 2>&1 || \
        die "Cannot reach '$remote_name:$probe_lead${probe_rest%%/*}' — network down, bad key, or no such bucket."
fi
[ -d "$STAGE_PARENT" ] || die "Staging area '$STAGE_PARENT' is not a directory."
[ -w "$STAGE_PARENT" ] || die "Staging area '$STAGE_PARENT' is not writable."

# Space, checked before starting rather than discovered half way. The archive
# compresses badly (blobs are already gzipped), so source size plus a fifth is
# a fair estimate of what this needs.
need_kb=$(( $(du -sk "$SOURCE" | cut -f1) * 12 / 10 ))
free_kb=$(df -Pk "$STAGE_PARENT" | awk 'NR==2 {print $4}')
[ "$free_kb" -ge "$need_kb" ] || \
    die "Need ~${need_kb}KB, only ${free_kb}KB free at '$STAGE_PARENT'."

# Read the delimiters from the plugin rather than repeating them here. If they
# drifted apart, fsck would silently find no heads to check and report a clean
# copy — a check that quietly stops checking is worse than no check.
HEADS_BEGIN="$("$PYTHON" -c 'from kibitzr_archive import integrity; print(integrity.HEADS_BEGIN)')"
HEADS_END="$("$PYTHON" -c 'from kibitzr_archive import integrity; print(integrity.HEADS_END)')"

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

The combined chain heads at the moment this copy was taken are below, and
fsck asserts the restored archive still computes them. That catches
restoring a different backup than you meant, or a directory assembled out
of two of them — neither of which any other check would notice.

$HEADS_BEGIN
$(cd "$STAGING" && "$PYTHON" -c '
import sys
from kibitzr_archive.store import ArchiveStore
store = ArchiveStore(".")
for name in store.check_names():
    sys.stdout.write("%s  %s\n" % (store.combined_head(name), name))
')
$HEADS_END
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
size="$(du -sh "$FINAL" | cut -f1)"

if [ -z "$REMOTE" ]; then
    trap - ERR
    echo ""
    echo "== Backup complete and checked: $FINAL"
    echo "   $size — chains recomputed, blobs and proofs all present."
    exit 0
fi

# -- remote ---------------------------------------------------------------
#
# Everything above has already produced a locally verified copy. From here the
# only question is whether it arrived intact, which is a hash comparison.
TARGET="$DEST_PARENT/evidence-archive-$STAMP"

# A partial copy left in scratch after a failed upload is noise, not evidence:
# the archive it came from is still on this disk. Clean it up on the way out,
# but only once the upload has been dealt with one way or the other.
cleanup_stage() { rm -rf "$FINAL"; }

echo ""
echo "== Uploading to $TARGET"
"$RCLONE" copy "$FINAL" "$TARGET" --transfers 4 --stats-one-line || {
    cleanup_stage
    die "Upload to '$TARGET' failed."
}

# The remote's own hashes against ours. This is the step that makes untrusted
# storage acceptable: a file that arrived corrupted does not match, and
# anything subtler is caught by fsck when the copy is restored.
echo ""
echo "== Comparing hashes with the remote"
"$RCLONE" check "$FINAL" "$TARGET" --one-way || {
    cleanup_stage
    die "The uploaded copy does not match. Remote left WITHOUT its completion marker."
}

# The marker stands in for the rename, which is unavailable: a backup key
# should not hold delete permission, and a rename is a copy plus a delete. A
# remote directory lacking this file is a known-incomplete upload.
marker="$(mktemp -d)/BACKUP-COMPLETE.txt"
cat > "$marker" <<EOF
Upload completed and hash-checked $(date -u +%Y-%m-%dT%H:%M:%SZ)
by $(hostname), from $SOURCE.

The absence of this file means the upload did not finish. Its presence
means every file arrived with a matching hash — it says nothing about the
archive's own integrity, which is what "kibitzr archive fsck" is for once
this directory has been fetched back.
EOF
"$RCLONE" copy "$marker" "$TARGET" || {
    rm -rf "$(dirname "$marker")"
    cleanup_stage
    die "Uploaded and checked, but the completion marker did not write."
}
rm -rf "$(dirname "$marker")"
cleanup_stage

trap - ERR
echo ""
echo "== Backup complete, uploaded and hash-checked: $TARGET"
echo "   $size — chains recomputed, blobs and proofs all present, all files matched."
