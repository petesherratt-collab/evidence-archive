"""External timestamping of chain heads, via OpenTimestamps.

The hash chains establish that the archive is internally consistent and
ordered. They establish nothing about *when* any of it existed: a self-
consistent history can be fabricated in its entirety after the fact, and a
chain computed by the archivist attests only to the archivist's arithmetic.
Until a head has been committed to something outside this machine, the archive
is a well-built database, not evidence.

Anchoring is the one item here whose cost of delay is unrecoverable. A gap in
collection can sometimes be backfilled from a third-party cache; time that has
already passed unattested can never be proven retroactively. So this runs from
the first day rather than after the schema settles — which is safe, because
proofs accumulate. An anchor over the heads as they are today proves those
heads existed today, permanently, whatever the schema does afterwards. A later
anchor is an additional proof, never a replacement.

**Why OpenTimestamps rather than an RFC 3161 authority.** A TSA's token is only
as good as continued trust in that authority and its keys; OTS commits to the
Bitcoin blockchain, so verification depends on public data rather than on an
institution remaining honest and solvent. For an archive whose entire premise is
removing the need to trust its keeper, importing a new party to trust would be
the wrong shape. The client is also free, keyless and rate-limit friendly.

**What is stamped is a manifest, not a bare digest.** A proof over an opaque
32-byte value tells a third party nothing about what was committed to. The
manifest is canonical JSON naming each check and its constituent chain heads,
so someone holding the proof and the manifest can see exactly what was attested
without running any of this code.

Proofs are two-stage. `stamp` returns quickly with calendar attestations; the
Bitcoin attestation appears once a block confirms, hours later. `upgrade` is
what converts the first into the second, and until it succeeds a proof depends
on the calendar operators. Both states are recorded honestly rather than a
pending proof being reported as complete.
"""
import json
import logging
import os
import shutil
import subprocess

from .store import COMBINED_HEAD_VERSION, sha256_hex, utc_now_iso


logger = logging.getLogger(__name__)

ANCHOR_DIR = "anchors"
METHOD = "opentimestamps"
MANIFEST_VERSION = 1

#: How long to wait on the calendars. Stamping contacts several remote
#: services; a slow one must not wedge a scheduled run.
STAMP_TIMEOUT = 180
UPGRADE_TIMEOUT = 180
VERIFY_TIMEOUT = 180


class AnchorError(Exception):
    """Raised when the external timestamping tool cannot be used."""


class ProofFormatError(Exception):
    """Raised when a ``.ots`` file cannot be read as an OpenTimestamps proof."""


#: Serialisation header every detached OTS proof starts with.
OTS_MAGIC = (b"\x00OpenTimestamps\x00\x00Proof\x00"
             b"\xbf\x89\xe2\xe8\x84\xe8\x92\x94")

#: Op tag -> (name, digest length). The tag names the hash the proof was taken
#: over; only the length is needed to read the digest out.
OTS_HASH_OPS = {
    0x02: ("sha1", 20),
    0x03: ("ripemd160", 20),
    0x08: ("sha256", 32),
    0x67: ("keccak256", 32),
}


def _read_varint(data, offset):
    """Read OTS's base-128 varint (LSB first, high bit continues)."""
    value = shift = 0
    while True:
        if offset >= len(data):
            raise ProofFormatError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7


def committed_digest(raw):
    """Return (algorithm, hexdigest) of the file a detached proof covers.

    This is the link that makes the trust chain start at the proof rather than
    at anything in ``polls.db``. A detached OTS proof carries, right after its
    header, the digest of the file it was stamped over; everything after that
    is the path from that digest to a Bitcoin block. So *which bytes a proof
    covers* is readable offline, with no OpenTimestamps client and no Bitcoin
    node, and comparing it to the manifest on disk is what stops an altered
    manifest being blessed by a matching digest in the ``anchor`` table.

    What this does **not** establish is *when*. A proof re-stamped today over a
    forged manifest would satisfy this check and carry today's attestation
    instead of the original's; separating those needs ``ots verify`` and the
    block time it reports. See ``deploy/VERIFYING.md``.
    """
    if not raw.startswith(OTS_MAGIC):
        raise ProofFormatError("not an OpenTimestamps detached proof")
    offset = len(OTS_MAGIC)
    _version, offset = _read_varint(raw, offset)
    if offset >= len(raw):
        raise ProofFormatError("truncated before the file hash operation")
    tag = raw[offset]
    offset += 1
    if tag not in OTS_HASH_OPS:
        raise ProofFormatError(f"unknown file hash op {tag:#04x}")
    name, length = OTS_HASH_OPS[tag]
    digest = raw[offset:offset + length]
    if len(digest) != length:
        raise ProofFormatError(f"truncated {name} digest")
    return name, digest.hex()


def find_ots(explicit=None):
    """Locate the OpenTimestamps client.

    Deliberately not vendored into the collector's virtualenv: anchoring must
    never be able to perturb the environment that does the collecting. The
    default layout keeps it in a sibling venv.
    """
    candidates = [
        explicit,
        os.environ.get("KIBITZR_ARCHIVE_OTS"),
        os.path.join(os.path.expanduser("~"),
                     "evidence-collection", ".venv-anchor", "bin", "ots"),
        shutil.which("ots"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    raise AnchorError(
        "OpenTimestamps client not found. Install it outside the collector's "
        "virtualenv, e.g.\n"
        "    python3 -m venv .venv-anchor\n"
        "    ./.venv-anchor/bin/pip install opentimestamps-client\n"
        "or pass --ots / set KIBITZR_ARCHIVE_OTS."
    )


def anchor_dir(store):
    path = os.path.join(store.root, ANCHOR_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def build_manifest(store, check_names, created_at=None):
    """Return (manifest_dict, canonical_bytes) committing to every check's heads.

    Canonical JSON — sorted keys, no insignificant whitespace, UTF-8 — so that
    the bytes covered by the proof are reproducible by anyone holding the same
    values.
    """
    created_at = created_at or utc_now_iso()
    entries = []
    for name in sorted(check_names):
        components = store.head_components(name)
        if components is None:
            logger.warning("Check %r has nothing recorded; not anchoring it.",
                           name)
            continue
        entries.append(components)
    if not entries:
        raise AnchorError("Nothing to anchor: no check has any recorded poll.")

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at": created_at,
        "combined_head_version": COMBINED_HEAD_VERSION,
        "checks": entries,
        "what_this_proves":
            "Each listed head existed no later than the Bitcoin block this "
            "proof attests to. It does not prove the archive is complete, nor "
            "that any statement in it about the world is true.",
        "how_to_verify":
            "Recompute each check's chain from the poll log and compare the "
            "heads; then verify the accompanying .ots proof over the canonical "
            "bytes of this file. See deploy/VERIFYING.md in the repository.",
    }
    # Same canonicalisation as the record hashes: sorted keys, no insignificant
    # whitespace, and ensure_ascii left at its default so the output is pure
    # ASCII. One rule for the whole archive — two would be a specification
    # defect, and it matters here because check names contain non-ASCII
    # characters (£, em dash) that would otherwise be encoded one way inside a
    # record hash and another inside the manifest committing to it.
    encoded = json.dumps(manifest, sort_keys=True,
                         separators=(',', ':')).encode("ascii")
    return manifest, encoded


def _run(argv, timeout):
    """Run a subprocess, returning (returncode, combined output)."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s"
    except OSError as exc:
        raise AnchorError(f"could not run {argv[0]!r}: {exc}") from exc
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def stamp(store, check_names, ots=None, created_at=None):
    """Anchor the current heads of the named checks. Returns a summary dict.

    One manifest and one proof cover the whole batch; a row is recorded per
    check so that each check's exposure can be reported separately.
    """
    ots = find_ots(ots)
    manifest, encoded = build_manifest(store, check_names,
                                       created_at=created_at)
    digest = sha256_hex(encoded)

    stamp_name = manifest["created_at"].replace(":", "-")
    manifest_ref = os.path.join(ANCHOR_DIR, f"{stamp_name}.json")
    manifest_path = os.path.join(store.root, manifest_ref)
    anchor_dir(store)
    # Written before stamping and never rewritten: the proof covers these exact
    # bytes, so any later edit would silently invalidate it.
    with open(manifest_path, "wb") as handle:
        handle.write(encoded)

    code, output = _run([ots, "stamp", manifest_path], STAMP_TIMEOUT)
    proof_path = manifest_path + ".ots"
    proof_ref = manifest_ref + ".ots" if os.path.exists(proof_path) else None
    status = "pending" if proof_ref else "failed"
    if code != 0 and proof_ref is None:
        logger.error("ots stamp failed: %s", output)

    # A manifest without a proof is not an anchor. Keeping it in anchors/ and
    # indexing it in the anchor table creates an impossible completeness
    # contract: fsck must report a missing proof, but the failed attempt never
    # produced one. Record the attempt on the append-only annotation chain and
    # remove the unattested manifest instead. A later successful retry creates
    # a new manifest at its honest later time.
    if proof_ref is None:
        store.record_annotation("note", {
            "event": "anchor_attempt_failed",
            "attempted_manifest": manifest_ref,
            "manifest_sha256": digest,
            "checks": [entry["check"] for entry in manifest["checks"]],
            "ots_output": output,
            "returncode": code,
        }, effective_from=manifest["created_at"])
        try:
            os.remove(manifest_path)
        except FileNotFoundError:
            pass
        return {
            "status": "failed",
            "manifest_ref": None,
            "attempted_manifest_ref": manifest_ref,
            "manifest_sha256": digest,
            "proof_ref": None,
            "checks": [entry["check"] for entry in manifest["checks"]],
            "anchor_ids": [],
            "output": output,
        }

    recorded = []
    for components in manifest["checks"]:
        recorded.append(store.record_anchor(
            components, METHOD, manifest_ref, digest,
            proof_ref=proof_ref, status=status,
            detail={"ots_output": output, "returncode": code},
            anchored_at=manifest["created_at"],
        ))

    return {
        "status": status,
        "manifest_ref": manifest_ref,
        "attempted_manifest_ref": manifest_ref,
        "manifest_sha256": digest,
        "proof_ref": proof_ref,
        "checks": [entry["check"] for entry in manifest["checks"]],
        "anchor_ids": recorded,
        "output": output,
    }


def upgrade(store, ots=None):
    """Upgrade pending proofs from calendar to Bitcoin attestation.

    Until this succeeds a proof rests on the calendar operators rather than on
    the blockchain, which is a weaker claim and is reported as such.
    """
    ots = find_ots(ots)
    results = []
    pending = {row["manifest_ref"] for row in store.anchors(status="pending")
               if row["proof_ref"]}
    for manifest_ref in sorted(pending):
        proof_path = os.path.join(store.root, manifest_ref + ".ots")
        if not os.path.exists(proof_path):
            store.set_anchor_status(manifest_ref, "failed",
                                    detail={"error": "proof file missing"})
            results.append((manifest_ref, "failed", "proof file missing"))
            continue
        code, output = _run([ots, "upgrade", proof_path], UPGRADE_TIMEOUT)
        # The client reports an already-complete or newly-completed proof on
        # stdout; anything else means it is still waiting for a block.
        complete = code == 0 and "pending" not in output.lower()
        if complete:
            store.set_anchor_status(manifest_ref, "complete",
                                    detail={"ots_output": output})
            results.append((manifest_ref, "complete", output))
        else:
            results.append((manifest_ref, "pending", output))
    return results


def reconcile_failed_attempts(store):
    """Move legacy proof-less attempts out of the anchor namespace.

    Releases before 0.2.1 retained a manifest and anchor-table rows when
    ``ots stamp`` produced no proof. That made fsck correctly report a missing
    proof forever. Preserve the attempt as an append-only annotation and as an
    unattested file outside ``anchors/``, then remove only its failed index
    rows. Anything with a proof, or any non-failed row, is refused.
    """
    failed = {}
    for row in store.anchors(status="failed"):
        failed.setdefault(row["manifest_ref"], []).append(row)

    moved = []
    destination_root = os.path.join(store.root, "failed-anchor-attempts")
    for manifest_ref, rows in sorted(failed.items()):
        if any(row["proof_ref"] for row in rows):
            raise AnchorError(
                f"refusing {manifest_ref}: a failed row still names a proof")
        if any(row["status"] != "failed" for row in store.anchors()
               if row["manifest_ref"] == manifest_ref):
            raise AnchorError(
                f"refusing {manifest_ref}: it also has a non-failed row")

        manifest_path = os.path.join(store.root, manifest_ref)
        proof_path = manifest_path + ".ots"
        if os.path.exists(proof_path):
            raise AnchorError(
                f"refusing {manifest_ref}: a proof exists at {proof_path}")

        detail = {
            "event": "legacy_failed_anchor_reconciled",
            "manifest_ref": manifest_ref,
            "manifest_sha256": rows[0]["manifest_sha256"],
            "checks": sorted(row["check_name"] for row in rows),
            "original_detail": rows[0]["detail"],
            "reason": ("ots stamp produced no proof; legacy code indexed the "
                       "attempt as an anchor, making fsck report it missing"),
        }
        store.record_annotation(
            "correction", detail, effective_from=rows[0]["anchored_at"])

        destination = None
        if os.path.exists(manifest_path):
            os.makedirs(destination_root, exist_ok=True)
            destination = os.path.join(
                destination_root, os.path.basename(manifest_ref))
            if os.path.exists(destination):
                raise AnchorError(
                    f"refusing {manifest_ref}: {destination} already exists")
            os.replace(manifest_path, destination)

        with store._connect() as conn:  # noqa: SLF001 - maintenance operation
            conn.execute(
                "DELETE FROM anchor WHERE manifest_ref = ? AND status = ?"
                " AND proof_ref IS NULL", (manifest_ref, "failed"))
        moved.append({
            "manifest_ref": manifest_ref,
            "preserved_as": destination,
            "rows": len(rows),
        })
    return moved


def verify(store, manifest_ref, ots=None):
    """Verify one proof, and that the manifest still matches what was recorded.

    Two separate questions, both worth asking: does the external proof still
    check out, and is the manifest on disk the one that was stamped?
    """
    ots = find_ots(ots)
    manifest_path = os.path.join(store.root, manifest_ref)
    proof_path = manifest_path + ".ots"
    if not os.path.exists(proof_path):
        return {"ok": False, "reason": f"no proof at {proof_path}"}

    with open(manifest_path, "rb") as handle:
        on_disk = sha256_hex(handle.read())
    rows = [row for row in store.anchors() if row["manifest_ref"] == manifest_ref]
    recorded = rows[0]["manifest_sha256"] if rows else None
    if recorded and recorded != on_disk:
        return {
            "ok": False,
            "reason": ("manifest on disk does not match the digest recorded "
                       f"when it was stamped ({on_disk} != {recorded})"),
        }

    code, output = _run([ots, "verify", proof_path], VERIFY_TIMEOUT)
    return {
        "ok": code == 0,
        "manifest_sha256": on_disk,
        "output": output,
        "checks": [row["check_name"] for row in rows],
    }
