"""Integrity checks over the parts of the archive the chains do not cover.

The governing case for this whole module is the first test: an archive whose
retained responses have all been lost still passes `verify` with every chain
intact. That is not a bug in `verify` — the chains cover polls.db and say so —
but it is exactly the report a half-finished backup produces, and believing it
is how a copy that holds no evidence gets treated as a good one.
"""
import gzip
import json
import os

import click
import pytest
from click.testing import CliRunner

from kibitzr_archive import integrity
from kibitzr_archive.cli import extend_cli
from kibitzr_archive.store import ArchiveStore, sha256_hex


@pytest.fixture
def store(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    store.record_poll("ctf", url="http://x", content=b"first")
    store.record_poll("ctf", url="http://x", content=b"second")
    store.record_poll("fts", url="http://y", content=b"other")
    return store


@pytest.fixture
def cli():
    @click.group()
    def root():
        pass

    extend_cli(root)
    return root


def _run(cli, root, *args):
    return CliRunner().invoke(cli, ["archive", *args, "--root", root])


def _anchor(store, check_name="ctf"):
    """Record an anchor with a real manifest on disk, without invoking ots."""
    from kibitzr_archive import anchor as anchor_mod

    manifest, encoded = anchor_mod.build_manifest(store, [check_name])
    os.makedirs(os.path.join(store.root, anchor_mod.ANCHOR_DIR), exist_ok=True)
    manifest_ref = os.path.join(anchor_mod.ANCHOR_DIR, "proof.json")
    with open(os.path.join(store.root, manifest_ref), "wb") as fp:
        fp.write(encoded)
    proof_ref = manifest_ref + ".ots"
    with open(os.path.join(store.root, proof_ref), "wb") as fp:
        fp.write(b"not a real proof")
    store.record_anchor(store.head_components(check_name), "opentimestamps",
                        manifest_ref, sha256_hex(encoded), proof_ref=proof_ref)
    return manifest_ref, proof_ref


# -- the gap this exists to close ----------------------------------------

def test_an_archive_with_no_blobs_at_all_still_verifies(store, cli):
    """The premise. Every chain recomputes from polls.db, so deleting every
    retained response leaves all of them intact — a clean bill of health for
    an archive that has lost the bytes a third party would re-derive from."""
    for dirpath, _dirs, files in os.walk(store.blob_root):
        for name in files:
            os.remove(os.path.join(dirpath, name))

    result = _run(cli, store.root, "verify")

    assert result.exit_code == 0
    assert "chain(s) intact" in result.output


def test_fsck_catches_what_verify_blessed(store, cli):
    """Same archive, the answer that is actually true of it."""
    for dirpath, _dirs, files in os.walk(store.blob_root):
        for name in files:
            os.remove(os.path.join(dirpath, name))

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 1
    assert "missing blob" in result.output
    assert "do not treat it as a good copy" in result.output


def test_verify_points_at_fsck_rather_than_implying_completeness(store, cli):
    """A passing `verify` should not be readable as "nothing is missing"."""
    result = _run(cli, store.root, "verify")

    assert "polls.db alone" in result.output
    assert "fsck" in result.output


# -- blobs ---------------------------------------------------------------

def test_a_sound_archive_passes(store, cli):
    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 0
    assert "every recorded proof is present" in result.output


def test_a_truncated_copy_is_caught(store, cli):
    """The removable-media failure: the copy stopped part-way through."""
    digest = sorted(store.referenced_blobs())[0]
    os.remove(store.blob_path(digest))

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 1
    assert digest[:12] in result.output


def test_a_blob_whose_bytes_no_longer_match_its_name_is_caught(store):
    """Content addressing is the whole of blob integrity: altered bytes stop
    hashing to the filename the log points at."""
    digest = sorted(store.referenced_blobs())[0]
    with gzip.open(store.blob_path(digest), "wb") as fp:
        fp.write(b"substituted")

    findings, _counts = integrity.check(store)

    assert [f.kind for f in integrity.broken(findings)] == ["corrupt blob"]


def test_a_blob_that_will_not_decompress_is_caught(store):
    """Media corruption usually presents as unreadable, not as wrong bytes."""
    digest = sorted(store.referenced_blobs())[0]
    with open(store.blob_path(digest), "wb") as fp:
        fp.write(b"\x00\x00 not gzip at all")

    findings, _counts = integrity.check(store)

    assert [f.kind for f in integrity.broken(findings)] == ["unreadable blob"]


def test_an_orphan_blob_is_noted_but_does_not_fail(store, cli):
    """A crash between put_blob and its INSERT leaves one of these. Nothing
    has been lost, so failing on it would train the reader to ignore fsck."""
    # Filed correctly under its own digest — an orphan, not a corruption.
    store.put_blob(b"unreferenced")

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 0
    assert "orphan blob" in result.output


def test_an_interrupted_write_leaves_a_stray_that_is_reported(store, cli):
    path = store.blob_path("a" * 64) + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fp:
        fp.write("partial")

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 0
    assert "stray file" in result.output


# -- anchors -------------------------------------------------------------

def test_a_missing_proof_file_is_a_failure(store, cli):
    _manifest_ref, proof_ref = _anchor(store)
    os.remove(os.path.join(store.root, proof_ref))

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 1
    assert "missing proof" in result.output


def test_a_missing_manifest_is_a_failure(store, cli):
    manifest_ref, _proof_ref = _anchor(store)
    os.remove(os.path.join(store.root, manifest_ref))

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 1
    assert "missing manifest" in result.output


def test_an_edited_manifest_no_longer_matches_its_proof(store, cli):
    """The proof covers specific bytes. Changing them silently detaches the
    two, and every other command would carry on reporting the anchor."""
    manifest_ref, _proof_ref = _anchor(store)
    path = os.path.join(store.root, manifest_ref)
    manifest = json.loads(open(path).read())
    manifest["created_at"] = "2020-01-01T00:00:00+00:00"
    with open(path, "w") as fp:
        json.dump(manifest, fp, sort_keys=True, separators=(',', ':'))

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 1
    assert "altered manifest" in result.output


def test_one_missing_proof_is_reported_once_not_once_per_check(store, cli):
    """A real anchor covers every check in a single manifest, so the live
    archive has five rows per proof. Reporting the same absent file five times
    buries the other findings and inflates the count a reader judges severity
    by."""
    from kibitzr_archive import anchor as anchor_mod

    manifest, encoded = anchor_mod.build_manifest(store, ["ctf", "fts"])
    os.makedirs(os.path.join(store.root, anchor_mod.ANCHOR_DIR), exist_ok=True)
    manifest_ref = os.path.join(anchor_mod.ANCHOR_DIR, "shared.json")
    with open(os.path.join(store.root, manifest_ref), "wb") as fp:
        fp.write(encoded)
    for name in ("ctf", "fts"):
        store.record_anchor(store.head_components(name), "opentimestamps",
                            manifest_ref, sha256_hex(encoded),
                            proof_ref=manifest_ref + ".ots")

    findings, _counts = integrity.check(store)

    kinds = [f.kind for f in integrity.broken(findings)]
    assert kinds == ["missing proof"], kinds
    assert "2 check(s)" in findings[0].detail


def test_the_ots_upgrade_backup_file_is_not_a_stray(store, cli):
    """`ots upgrade` leaves the pre-upgrade proof beside the new one. Those
    are ours and are worth keeping, so they must not read as debris."""
    _manifest_ref, proof_ref = _anchor(store)
    with open(os.path.join(store.root, proof_ref + ".bak"), "wb") as fp:
        fp.write(b"pre-upgrade proof")

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 0
    assert "unrecorded proof file" not in result.output


def test_an_unrecorded_file_in_the_anchor_dir_is_noted(store, cli):
    _anchor(store)
    with open(os.path.join(store.root, "anchors", "mystery.json"), "w") as fp:
        fp.write("{}")

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 0
    assert "unrecorded proof file" in result.output


# -- the log and the proofs from different moments -----------------------

def test_a_log_older_than_its_proofs_is_caught(store, cli):
    """Restore polls.db from an older copy than the anchors/ beside it and
    both halves stay internally consistent: every chain verifies, every proof
    still validates over its manifest. Only the seam between them shows it."""
    _anchor(store)
    last_id = store.last_poll_id("ctf")
    with store._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM poll WHERE id = ?", (last_id,))

    chains = _run(cli, store.root, "verify")
    assert chains.exit_code == 0, "premise: the truncated log still verifies"

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 1
    assert "anchor ahead of log" in result.output


def test_a_rewritten_row_under_an_anchor_is_caught(store, cli):
    """The row is still there and still hashes consistently for its own
    chain, but it is no longer the row the proof was taken over."""
    _anchor(store)
    last_id = store.last_poll_id("ctf")
    with store._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE poll SET record_hash = ? WHERE id = ?",
                     ("f" * 64, last_id))

    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 1
    assert "head mismatch" in result.output


# -- grading -------------------------------------------------------------

def test_polls_awaiting_a_proof_do_not_fail_the_check(store, cli):
    """Every archive is in this state for most of its life. Grading it as a
    failure would make fsck fail continuously and so be ignored."""
    result = _run(cli, store.root, "fsck")

    assert result.exit_code == 0
    assert "not yet covered by a proof" in result.output


def test_check_is_read_only(store):
    """Safe to run against a live collector, and against a backup."""
    before = {
        path: os.stat(os.path.join(dirpath, path)).st_mtime
        for dirpath, _dirs, files in os.walk(store.root) for path in files
    }

    integrity.check(store)

    after = {
        path: os.stat(os.path.join(dirpath, path)).st_mtime
        for dirpath, _dirs, files in os.walk(store.root) for path in files
    }
    assert before == after
