"""Whole-archive integrity — the part the hash chains structurally cannot see.

``archive verify`` recomputes the poll, normalisation and annotation chains.
All three read ``polls.db`` and nothing else, which is the correct scope for
what they claim: they detect an edited or truncated *log*.

But it means an archive whose ``blobs/`` directory is empty and whose
``anchors/`` directory is missing verifies clean and reports every chain
intact — having lost every retained response and every external proof. The
chains are undamaged because the chains never covered those bytes.

That is not a hypothetical failure. It is the expected result of a copy to
removable media that filled up or was unplugged part-way, which is precisely
the moment someone is relying on the answer. ``raw_ref`` is folded into a
record hash, so the log can prove the *reference* was not tampered with, and
can say nothing at all about whether the file it names still exists.

So this module checks the obligations the log implies but does not police:

  * every ``raw_ref`` in the log resolves to a file in ``blobs/``
  * every blob's bytes hash to the name it is filed under
  * every anchor's manifest is present and still matches its recorded digest
  * every anchor's proof file is present
  * each anchor's ``poll_head`` still matches the row the anchor names

That last one is the cross-check with no counterpart anywhere else. A proof
commits to a head at a stated ``last_poll_id``; if the log no longer produces
that head at that row, the log and the proofs beside it are from different
moments. This is the signature of a ``polls.db`` restored from an older copy
than the ``anchors/`` next to it, and both halves are internally consistent, so
``verify`` passes and every proof still validates.

Findings are graded, because "the archive is damaged" and "the archive is
untidy" call for different reactions:

  broken   something is gone or contradictory that cannot be re-derived.
  suspect  an inconsistency with no evidence provably lost — an orphaned blob
           left by a crash between ``put_blob`` and its INSERT is mess, not
           damage, and a poll not yet anchored is a normal state that every
           archive is in for most of its life.

Only ``broken`` fails the run. Grading un-anchored polls as broken would make
the check fail continuously and therefore be ignored, which is how a loud
check becomes a decoration.
"""
import gzip
import os
from collections import namedtuple

from .anchor import ANCHOR_DIR
from .store import sha256_hex


BROKEN = "broken"
SUSPECT = "suspect"

# `detail` is a whole sentence: this output is read by someone whose backup
# has just failed at the point they needed it, and a bare digest tells them
# nothing about what to do next.
Finding = namedtuple("Finding", "severity kind detail")


def _walk_blobs(store):
    """Return ({digest: path}, [stray relative paths]) for the blob store."""
    blobs, strays = {}, []
    if not os.path.isdir(store.blob_root):
        return blobs, strays
    for dirpath, _dirnames, filenames in os.walk(store.blob_root):
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            if filename.endswith(".gz"):
                blobs[filename[:-len(".gz")]] = path
            else:
                strays.append(os.path.relpath(path, store.root))
    return blobs, strays


def _check_blobs(store, findings):
    """Reconcile the blob store against the references in the log."""
    on_disk, strays = _walk_blobs(store)
    referenced = store.referenced_blobs()

    for digest in sorted(referenced - set(on_disk)):
        findings.append(Finding(
            BROKEN, "missing blob",
            f"{digest[:12]} is referenced by the log but is not in blobs/; "
            f"that retained response is gone"))

    for digest in sorted(set(on_disk) - referenced):
        findings.append(Finding(
            SUSPECT, "orphan blob",
            f"{digest[:12]} is on disk but no poll row references it"))

    for stray in strays:
        findings.append(Finding(
            SUSPECT, "stray file",
            f"{stray} is not a blob; an interrupted write leaves .tmp behind"))

    # Content addressing makes this the whole of blob integrity. Bytes that
    # hash to the name they are filed under cannot have been altered without
    # also being renamed, and the name is what the log commits to.
    for digest in sorted(on_disk):
        try:
            with gzip.open(on_disk[digest], "rb") as fp:
                actual = sha256_hex(fp.read())
        except (OSError, EOFError) as exc:
            findings.append(Finding(
                BROKEN, "unreadable blob",
                f"{digest[:12]} could not be decompressed: {exc}"))
            continue
        if actual != digest:
            findings.append(Finding(
                BROKEN, "corrupt blob",
                f"{digest[:12]} holds bytes hashing to {actual[:12]}"))

    return len(on_disk), len(referenced)


def _check_anchors(store, findings):
    """Reconcile recorded anchors against the proof files and the log."""
    rows = store.anchors()
    expected = set()

    # One anchor covers every check in a single manifest, so a proof taken
    # over five checks is five rows sharing one pair of files. Group before
    # touching the disk: reporting one missing file five times both buries the
    # other findings and inflates the count a reader uses to judge severity.
    manifests = {}
    for row in rows:
        manifests.setdefault(row["manifest_ref"], []).append(row)

    for manifest_ref, group in sorted(manifests.items()):
        expected.add(manifest_ref)
        covers = f"covering {len(group)} check(s)"
        try:
            with open(os.path.join(store.root, manifest_ref), "rb") as fp:
                raw = fp.read()
        except OSError:
            findings.append(Finding(
                BROKEN, "missing manifest",
                f"{manifest_ref} is recorded as anchored {covers} but is not "
                f"on disk"))
        else:
            if sha256_hex(raw) != group[0]["manifest_sha256"]:
                findings.append(Finding(
                    BROKEN, "altered manifest",
                    f"{manifest_ref} no longer hashes to the value its proof "
                    f"was taken over"))

        proof_ref = group[0]["proof_ref"]
        if proof_ref:
            # `ots upgrade` rewrites the proof in place and leaves the
            # pre-upgrade version beside it. Both are ours; neither is a stray.
            expected.update((proof_ref, proof_ref + ".bak"))
            if not os.path.exists(os.path.join(store.root, proof_ref)):
                findings.append(Finding(
                    BROKEN, "missing proof",
                    f"{proof_ref} is recorded but absent; without it the "
                    f"manifest asserts nothing about time for {len(group)} "
                    f"check(s)"))

    # This one is genuinely per-row: each check has its own head, and the seam
    # between the two halves of the archive. See the module docstring.
    for row in rows:
        if row["last_poll_id"] is not None:
            actual = store.poll_record_hash(row["last_poll_id"])
            if actual is None:
                findings.append(Finding(
                    BROKEN, "anchor ahead of log",
                    f"{row['check_name']}: a proof covers poll id "
                    f"{row['last_poll_id']}, which this log does not contain"))
            elif actual != row["poll_head"]:
                findings.append(Finding(
                    BROKEN, "head mismatch",
                    f"{row['check_name']}: poll id {row['last_poll_id']} now "
                    f"hashes to {actual[:12]}, not the anchored "
                    f"{row['poll_head'][:12]}"))

    anchor_root = os.path.join(store.root, ANCHOR_DIR)
    if os.path.isdir(anchor_root):
        for filename in sorted(os.listdir(anchor_root)):
            ref = os.path.join(ANCHOR_DIR, filename)
            if ref not in expected:
                findings.append(Finding(
                    SUSPECT, "unrecorded proof file",
                    f"{ref} is in {ANCHOR_DIR}/ but no anchor row names it"))

    return len(rows)


def check(store):
    """Inspect the archive at ``store``; return (findings, counts).

    Read-only throughout. Nothing here writes to the archive, so it is safe
    against a live collector and safe to point at a backup.
    """
    findings = []
    blobs, referenced = _check_blobs(store, findings)
    anchors = _check_anchors(store, findings)

    # Exposure, not damage — see the grading note in the module docstring.
    # `archive anchors` reports this in full; it appears here so that one
    # command can answer "is this copy sound and is it fully covered".
    exposed = sum(store.unanchored_polls(name) for name in store.check_names())
    if exposed:
        findings.append(Finding(
            SUSPECT, "unanchored polls",
            f"{exposed} poll(s) are covered by no proof; run `archive anchor`"))

    return findings, {
        "blobs": blobs,
        "referenced": referenced,
        "anchors": anchors,
        "exposed": exposed,
    }


def broken(findings):
    """The findings that mean evidence is gone rather than merely untidy."""
    return [f for f in findings if f.severity == BROKEN]
