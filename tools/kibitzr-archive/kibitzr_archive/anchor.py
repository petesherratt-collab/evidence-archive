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
