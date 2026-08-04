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
the moment someone is relying on the answer. The chains cannot see it because
they never covered those bytes: a poll row commits to ``content_sha256``, the
digest of the response, and says nothing about whether a file holding those
bytes still exists.

This docstring used to claim ``raw_ref`` was folded into a record hash. It is
not, and never was — the hashed fields are listed in ``poll_hash_fields``, and
``raw_ref`` has never been among them. The claim mattered, because both this
module and ``deploy/verify_independently.py`` resolved retained responses
*through* ``raw_ref`` on the strength of it. That made the forgery in
``ArchiveStore.referenced_blobs`` possible, and it passed every check the
archive had. Blobs are now resolved by ``content_sha256``, which is hashed.

So this module checks the obligations the log implies but does not police:

  * every poll row's ``raw_ref`` still equals the ``content_sha256`` the chain
    commits to — an unattested second name for a response is not allowed to
    disagree with the attested one
  * every ``content_sha256`` in the log resolves to a file in ``blobs/``
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
import json
import os
from collections import namedtuple

from .anchor import ANCHOR_DIR
from .store import (COMBINED_HEAD_VERSION, GENESIS, combine_heads, sha256_hex)


BROKEN = "broken"
SUSPECT = "suspect"

# A backup writes its source's chain heads into this file, inside the copy.
# Restoring can then assert that the archive it rebuilt computes the heads the
# backup claimed — which catches restoring a different backup than you meant,
# or a directory assembled from two of them. Same principle as the
# anchor-to-poll_head cross-check, one layer out: the anchor seam catches a
# polls.db older than the anchors beside it, and this catches a whole archive
# that is not the one the manifest describes.
BACKUP_MANIFEST = "BACKUP.txt"
HEADS_BEGIN = "-----BEGIN CHAIN HEADS-----"
HEADS_END = "-----END CHAIN HEADS-----"

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
    """Reconcile the blob store against the digests the chain commits to.

    ``referenced`` comes from ``content_sha256``, the hashed field, not from
    ``raw_ref``, which nothing attests. See ``ArchiveStore.referenced_blobs``.
    """
    on_disk, strays = _walk_blobs(store)
    referenced = store.referenced_blobs()

    # An unattested second name for a retained response is the whole of the
    # forgery path this check used to be blind to, so it is reported before
    # anything else and graded as damage: the row no longer says which bytes
    # were observed.
    for row in store.unbound_blob_rows():
        findings.append(Finding(
            BROKEN, "unbound retained response",
            f"poll {row['id']} ({row['check_name']}, {row['polled_at']}) "
            f"names blob {str(row['raw_ref'])[:12]} but the chain commits to "
            f"content {str(row['content_sha256'])[:12]}; raw_ref is not "
            f"hashed, so the two disagreeing means the response on disk is "
            f"not the one this poll attests to"))

    for digest in sorted(referenced - set(on_disk)):
        findings.append(Finding(
            BROKEN, "missing blob",
            f"{digest[:12]} is committed to by the log but is not in blobs/; "
            f"that retained response is gone"))

    for digest in sorted(set(on_disk) - referenced):
        findings.append(Finding(
            SUSPECT, "orphan blob",
            f"{digest[:12]} is on disk but no poll row references it"))

    for stray in strays:
        findings.append(Finding(
            SUSPECT, "stray file",
            f"{stray} is not a blob; an interrupted write leaves .tmp behind"))

    # Content addressing makes this the whole of blob integrity, but only
    # because of what the name now is. Bytes that hash to the name they are
    # filed under cannot have been altered without also being renamed; the
    # names checked against the log are `content_sha256`, and that field is
    # hashed into the record chain. So for a referenced digest the three
    # conditions the archive actually needs — the blob is present, it hashes to
    # the digest, and that digest is the one the anchor covers — are all
    # established here. When the name came from `raw_ref` the last of the three
    # was missing, and it was the one that mattered.
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


class _Chains:
    """Chains recomputed from ``polls.db``, cached per check.

    Every head an anchor names is checked against these, never against the
    ``anchor`` table. Recomputing is the whole point: a stored ``record_hash``
    is a claim by whoever last wrote the row, and an attacker editing a row
    edits its stored hash in the same breath.
    """

    def __init__(self, store):
        self._store = store
        self._polls = {}
        self._norms = {}
        self._annotations = None

    def polls(self, check_name):
        """(ok, bad_row_id, {poll_id: recomputed_hash})."""
        if check_name not in self._polls:
            ok, bad, hashes = self._store.trace_chain(check_name)
            self._polls[check_name] = (ok, bad, dict(hashes))
        return self._polls[check_name]

    def norms(self, check_name):
        """(ok, bad_row_id, {recomputed hashes})."""
        if check_name not in self._norms:
            ok, bad, hashes = self._store.trace_normalisation_chain(check_name)
            self._norms[check_name] = (ok, bad, {h for _id, h in hashes})
        return self._norms[check_name]

    def annotations(self):
        if self._annotations is None:
            ok, bad, hashes = self._store.trace_annotation_chain()
            self._annotations = (ok, bad, {h for _id, h in hashes})
        return self._annotations


def _check_manifest_entry(chains, label, entry, findings):
    """Bind one manifest entry to the recomputed log."""
    name = entry.get("check")
    poll_head = entry.get("poll_head")
    last_id = entry.get("last_poll_id")

    poll_ok, poll_bad, poll_hashes = chains.polls(name)
    if not poll_hashes:
        findings.append(Finding(
            BROKEN, "anchor names unknown check",
            f"{label}: a proof covers {name!r}, which this log has no "
            f"verifiable polls for"))
        return
    if not poll_ok:
        findings.append(Finding(
            BROKEN, "anchored chain broken",
            f"{label}: {name}'s poll chain fails to recompute at row "
            f"{poll_bad}, so the head this proof covers cannot be reached"))

    # The seam. A proof commits to a head at a stated row; if the log no longer
    # produces that head at that row, the log and the proofs beside it are from
    # different moments — the signature of a polls.db restored from an older
    # copy than the anchors/ next to it, where both halves are internally
    # consistent and every proof still validates.
    if last_id is None:
        findings.append(Finding(
            BROKEN, "anchor names no row",
            f"{label}: the entry for {name} states no last_poll_id, so its "
            f"head cannot be located in the log"))
    elif last_id not in poll_hashes:
        findings.append(Finding(
            BROKEN, "anchor ahead of log",
            f"{label}: a proof covers {name} at poll id {last_id}, which this "
            f"log does not contain as a verified row"))
    elif poll_hashes[last_id] != poll_head:
        findings.append(Finding(
            BROKEN, "head mismatch",
            f"{label}: {name} poll id {last_id} recomputes to "
            f"{poll_hashes[last_id][:12]}, not the anchored "
            f"{str(poll_head)[:12]}"))

    # The other two heads are not pinned to a row id, so the requirement is
    # occurrence: the anchored value has to be a hash the rebuilt chain
    # actually produced. GENESIS means "nothing on this chain yet" and is
    # always admissible.
    norm_head = entry.get("norm_head")
    norm_ok, norm_bad, norm_hashes = chains.norms(name)
    if not norm_ok:
        findings.append(Finding(
            BROKEN, "anchored chain broken",
            f"{label}: {name}'s normalisation chain fails to recompute at row "
            f"{norm_bad}"))
    if norm_head != GENESIS and norm_head not in norm_hashes:
        findings.append(Finding(
            BROKEN, "anchored head not in chain",
            f"{label}: {name} is anchored at normalisation head "
            f"{str(norm_head)[:12]}, which does not occur in the "
            f"normalisation chain rebuilt from this log"))

    ann_head = entry.get("annotation_head")
    ann_ok, ann_bad, ann_hashes = chains.annotations()
    if not ann_ok:
        findings.append(Finding(
            BROKEN, "anchored chain broken",
            f"{label}: the annotation chain fails to recompute at row "
            f"{ann_bad}"))
    if ann_head != GENESIS and ann_head not in ann_hashes:
        findings.append(Finding(
            BROKEN, "anchored head not in chain",
            f"{label}: anchored at annotation head {str(ann_head)[:12]}, which "
            f"does not occur in the annotation chain rebuilt from this log — "
            f"an anchored correction cannot be withdrawn"))

    # Recomputed from the manifest's own parts, which have each just been
    # located in a rebuilt chain. Checking the manifest's arithmetic against
    # itself, which is all the independent verifier used to do, proves only
    # that the manifest is self-consistent.
    expected = combine_heads(poll_head, norm_head, ann_head,
                             version=entry.get("head_version",
                                               COMBINED_HEAD_VERSION))
    if expected != entry.get("combined_head"):
        findings.append(Finding(
            BROKEN, "combined head does not follow",
            f"{label}: the combined_head recorded for {name} is not what its "
            f"own poll, normalisation and annotation heads produce"))


def _check_anchors(store, findings):
    """Reconcile the anchor manifests on disk against the recomputed log.

    Driven from the manifest **files**, not from the ``anchor`` table. The
    table is a mutable index living in the same database as the rows an anchor
    exists to pin down, so an attacker who can rewrite a poll row can rewrite
    the anchor row that contradicts it — and this check used to compare the
    manifest against ``manifest_sha256`` from that same table, and the poll
    head against ``poll_head`` from it. Both comparisons were between two
    values under one writer's control.

    What is left of the table's role is completeness: it names manifests that
    ought to exist, so a deleted proof is still reported rather than simply
    vanishing along with the row that referenced it.
    """
    rows = store.anchors()
    chains = _Chains(store)
    expected = set()
    recorded = {row["manifest_ref"]: row for row in rows}

    anchor_root = os.path.join(store.root, ANCHOR_DIR)
    on_disk = sorted(
        os.path.join(ANCHOR_DIR, name)
        for name in (os.listdir(anchor_root) if os.path.isdir(anchor_root)
                     else [])
        if name.endswith(".json")
    )

    for manifest_ref in on_disk:
        label = os.path.basename(manifest_ref)
        try:
            with open(os.path.join(store.root, manifest_ref), "rb") as fp:
                raw = fp.read()
            manifest = json.loads(raw)
            entries = manifest["checks"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if manifest_ref in recorded:
                findings.append(Finding(
                    BROKEN, "unreadable manifest",
                    f"{manifest_ref} is recorded as anchored but cannot be "
                    f"read as a manifest: {exc}"))
                expected.add(manifest_ref)
            # Otherwise it is just a file someone left in anchors/. Nothing
            # references it and nothing is lost, so it falls through to the
            # unrecorded-file sweep below as mess rather than damage.
            continue

        expected.add(manifest_ref)

        # A tripwire, not a link in the trust chain. Both sides are mutable and
        # sit under one writer's control, so agreement proves nothing; what
        # proves the bytes are the stamped bytes is the .ots over them, which
        # `archive verify-anchor` runs and this cannot. It stays because a
        # disagreement is still worth stopping for — one of the two has been
        # changed since stamping, and the anchor no longer means what it says.
        # Reported without claiming which side moved.
        row = recorded.get(manifest_ref)
        if row is not None and sha256_hex(raw) != row["manifest_sha256"]:
            findings.append(Finding(
                BROKEN, "altered manifest",
                f"{manifest_ref} hashes to {sha256_hex(raw)[:12]}, but the "
                f"digest recorded when it was stamped is "
                f"{str(row['manifest_sha256'])[:12]}; the proof was taken over "
                f"one of these and cannot cover both"))

        # A manifest without its proof asserts nothing about time. It is
        # checked against the log regardless — a manifest that no longer
        # matches the log is worth reporting whether or not its proof survived.
        proof_ref = manifest_ref + ".ots"
        expected.update((proof_ref, proof_ref + ".bak"))
        if not os.path.exists(os.path.join(store.root, proof_ref)):
            findings.append(Finding(
                BROKEN, "missing proof",
                f"{proof_ref} is absent; without it the manifest asserts "
                f"nothing about time for {len(entries)} check(s)"))

        for entry in entries:
            _check_manifest_entry(chains, label, entry, findings)

    # The table as index, not as evidence: a manifest it names that is not on
    # disk is a deletion the manifests alone could not report.
    for manifest_ref in sorted({row["manifest_ref"] for row in rows}):
        if manifest_ref not in expected:
            covers = len([r for r in rows if r["manifest_ref"] == manifest_ref])
            findings.append(Finding(
                BROKEN, "missing manifest",
                f"{manifest_ref} is recorded as anchored covering {covers} "
                f"check(s) but is not on disk"))

    if os.path.isdir(anchor_root):
        for filename in sorted(os.listdir(anchor_root)):
            ref = os.path.join(ANCHOR_DIR, filename)
            if ref not in expected:
                findings.append(Finding(
                    SUSPECT, "unrecorded proof file",
                    f"{ref} is in {ANCHOR_DIR}/ but no manifest names it"))

    return len(rows)


def declared_heads(root):
    """Parse the chain heads a backup manifest claims, or {} if there is none.

    Absent manifest and absent heads block are both simply "nothing declared".
    A live archive has no manifest, and a backup taken before this existed has
    no block; neither is a fault, so neither is reported as one.
    """
    path = os.path.join(root, BACKUP_MANIFEST)
    if not os.path.exists(path):
        return {}
    heads, inside = {}, False
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.rstrip("\n")
            if line.strip() == HEADS_BEGIN:
                inside = True
            elif line.strip() == HEADS_END:
                break
            elif inside and line.strip():
                # "<hexdigest>  <check name>" — split once, because check
                # names contain spaces (and £, and em dashes).
                digest, _, name = line.strip().partition("  ")
                if name:
                    heads[name.strip()] = digest
    return heads


def _check_declared_heads(store, findings):
    """Compare the archive against the heads its backup manifest claims."""
    declared = declared_heads(store.root)
    if not declared:
        return 0

    present = set(store.check_names())
    for name in sorted(declared):
        if name not in present:
            findings.append(Finding(
                BROKEN, "check missing since backup",
                f"the manifest claims a head for {name!r}, which this archive "
                f"has no polls for at all"))
            continue
        actual = store.combined_head(name)
        if actual != declared[name]:
            findings.append(Finding(
                BROKEN, "head not as declared",
                f"{name}: this archive computes {str(actual)[:12]}, but the "
                f"backup manifest claims {declared[name][:12]} — this is not "
                f"the archive that manifest describes"))

    for name in sorted(present - set(declared)):
        findings.append(Finding(
            SUSPECT, "check absent from manifest",
            f"{name} has polls but the backup manifest does not mention it"))

    return len(declared)


def check(store):
    """Inspect the archive at ``store``; return (findings, counts).

    Read-only throughout. Nothing here writes to the archive, so it is safe
    against a live collector and safe to point at a backup.
    """
    findings = []
    blobs, referenced = _check_blobs(store, findings)
    anchors = _check_anchors(store, findings)
    declared = _check_declared_heads(store, findings)

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
        "declared_heads": declared,
    }


def broken(findings):
    """The findings that mean evidence is gone rather than merely untidy."""
    return [f for f in findings if f.severity == BROKEN]
