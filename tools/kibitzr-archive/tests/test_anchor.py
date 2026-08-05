"""Anchoring: what gets committed to, and how honestly it is reported.

No test here contacts a calendar. The external service is stubbed, because what
needs testing is what this code commits to and what it claims afterwards — not
whether OpenTimestamps works.
"""
import json
import os

import pytest

from kibitzr_archive import anchor as anchor_module
from kibitzr_archive.anchor import (
    AnchorError, build_manifest, reconcile_failed_attempts, stamp, upgrade,
)
from kibitzr_archive.store import GENESIS, ArchiveStore


@pytest.fixture
def store(tmp_path):
    return ArchiveStore(str(tmp_path / "archive"))


@pytest.fixture
def fake_ots(monkeypatch, tmp_path):
    """Stand in for the OpenTimestamps client."""
    calls = []

    def run(argv, timeout):
        calls.append(argv)
        if argv[1] == "stamp":
            with open(argv[2] + ".ots", "wb") as handle:
                handle.write(b"fake-proof")
            return 0, "Submitting to remote calendar"
        if argv[1] == "upgrade":
            return 0, run.upgrade_output
        return 0, "Success! Bitcoin block 900000 attests existence"

    run.upgrade_output = "Success! Timestamp complete"
    monkeypatch.setattr(anchor_module, "_run", run)
    monkeypatch.setattr(anchor_module, "find_ots", lambda explicit=None: "ots")
    run.calls = calls
    return run


# -- what the proof commits to --------------------------------------------

def test_manifest_records_the_components_not_just_the_digest(store):
    """An anchor must stay verifiable if combined_head's formula later changes,
    so it records the heads it committed to, not only the result."""
    store.record_poll("check", content="x")
    _, encoded = build_manifest(store, ["check"])
    manifest = json.loads(encoded)

    entry = manifest["checks"][0]
    assert entry["poll_head"] == store.head("check")
    assert entry["norm_head"] == GENESIS
    assert entry["annotation_head"] == GENESIS
    assert entry["combined_head"] == store.combined_head("check")
    assert entry["head_version"] == manifest["combined_head_version"]


def test_manifest_bytes_are_canonical(store):
    store.record_poll("check", content="x")
    _, first = build_manifest(store, ["check"], created_at="2026-08-02T00:00:00+00:00")
    _, second = build_manifest(store, ["check"], created_at="2026-08-02T00:00:00+00:00")

    assert first == second
    # Reproducible by anyone holding the same values: re-serialising the parsed
    # manifest with the documented rules must return the identical bytes, so
    # the proof covers something a third party can regenerate exactly.
    assert first == json.dumps(json.loads(first), sort_keys=True,
                               separators=(',', ':')).encode("ascii")


def test_manifest_canonicalisation_matches_the_record_hashes(store):
    """One canonicalisation rule for the whole archive.

    Check names here contain non-ASCII characters. If the manifest escaped them
    differently from the record hashes it commits to, the specification a third
    party has to implement would have two incompatible halves.
    """
    store.record_poll("Spend over £25k — monthly", content="x")
    _, encoded = build_manifest(store, ["Spend over £25k — monthly"])

    assert b"\\u00a3" in encoded and b"\\u2014" in encoded
    encoded.decode("ascii")  # must not raise


def test_manifest_covers_every_check_in_sorted_order(store):
    store.record_poll("b", content="x")
    store.record_poll("a", content="y")
    _, encoded = build_manifest(store, ["b", "a"])

    assert [c["check"] for c in json.loads(encoded)["checks"]] == ["a", "b"]


def test_checks_with_nothing_recorded_are_skipped_not_faked(store):
    store.record_poll("real", content="x")
    _, encoded = build_manifest(store, ["real", "never-polled"])

    assert [c["check"] for c in json.loads(encoded)["checks"]] == ["real"]


def test_anchoring_an_empty_archive_refuses(store):
    with pytest.raises(AnchorError):
        build_manifest(store, ["nothing"])


# -- recording and reporting ----------------------------------------------

def test_stamp_records_a_row_per_check_and_one_manifest(store, fake_ots):
    store.record_poll("a", content="x")
    store.record_poll("b", content="y")

    result = stamp(store, ["a", "b"])
    rows = store.anchors()

    assert result["status"] == "pending"
    assert len(rows) == 2
    assert {r["manifest_ref"] for r in rows} == {result["manifest_ref"]}
    assert all(r["status"] == "pending" for r in rows)


def test_manifest_digest_recorded_matches_the_bytes_stamped(store, fake_ots):
    store.record_poll("a", content="x")
    result = stamp(store, ["a"])

    from kibitzr_archive.store import sha256_hex
    with open(os.path.join(store.root, result["manifest_ref"]), "rb") as handle:
        assert sha256_hex(handle.read()) == result["manifest_sha256"]


def test_a_fresh_proof_is_pending_not_complete(store, fake_ots):
    """Reporting a calendar attestation as a Bitcoin one would overstate what
    the archive can prove."""
    store.record_poll("a", content="x")
    stamp(store, ["a"])

    assert {r["status"] for r in store.anchors()} == {"pending"}


def test_upgrade_promotes_only_when_the_proof_is_complete(store, fake_ots):
    store.record_poll("a", content="x")
    stamp(store, ["a"])

    fake_ots.upgrade_output = "Timestamp not yet complete; pending confirmation"
    upgrade(store)
    assert {r["status"] for r in store.anchors()} == {"pending"}

    fake_ots.upgrade_output = "Success! Timestamp complete"
    upgrade(store)
    assert {r["status"] for r in store.anchors()} == {"complete"}


def test_a_failed_stamp_is_annotated_without_claiming_an_anchor(
        store, monkeypatch):
    store.record_poll("a", content="x")
    monkeypatch.setattr(anchor_module, "find_ots", lambda explicit=None: "ots")
    monkeypatch.setattr(anchor_module, "_run",
                        lambda argv, timeout: (1, "all calendars unreachable"))

    result = stamp(store, ["a"])

    assert result["status"] == "failed"
    assert store.anchors() == []
    assert not os.path.exists(os.path.join(
        store.root, result["attempted_manifest_ref"]))
    notes = store.annotations(kind="note")
    assert len(notes) == 1
    assert notes[0]["detail"]["event"] == "anchor_attempt_failed"
    assert notes[0]["detail"]["manifest_sha256"] == result["manifest_sha256"]


def test_a_failed_anchor_does_not_count_as_coverage(store, monkeypatch):
    store.record_poll("a", content="x")
    monkeypatch.setattr(anchor_module, "find_ots", lambda explicit=None: "ots")
    monkeypatch.setattr(anchor_module, "_run",
                        lambda argv, timeout: (1, "unreachable"))
    stamp(store, ["a"])

    assert store.unanchored_polls("a") == 1


def test_legacy_failed_attempts_are_preserved_and_unindexed(store):
    """Repair old archives without deleting the evidence of the outage."""
    store.record_poll("a", content="x")
    manifest, encoded = build_manifest(
        store, ["a"], created_at="2026-08-05T00:00:00+00:00")
    manifest_ref = os.path.join("anchors", "legacy.json")
    os.makedirs(os.path.join(store.root, "anchors"), exist_ok=True)
    with open(os.path.join(store.root, manifest_ref), "wb") as handle:
        handle.write(encoded)
    from kibitzr_archive.store import sha256_hex
    store.record_anchor(
        manifest["checks"][0], "opentimestamps", manifest_ref,
        sha256_hex(encoded), proof_ref=None, status="failed",
        detail={"ots_output": "calendar outage"},
        anchored_at=manifest["created_at"],
    )

    repaired = reconcile_failed_attempts(store)

    assert repaired[0]["rows"] == 1
    assert store.anchors() == []
    assert not os.path.exists(os.path.join(store.root, manifest_ref))
    assert os.path.exists(os.path.join(
        store.root, "failed-anchor-attempts", "legacy.json"))
    correction = store.annotations(kind="correction")[0]
    assert correction["detail"]["event"] == "legacy_failed_anchor_reconciled"


# -- exposure reporting ----------------------------------------------------

def test_unanchored_count_is_what_has_no_proof_yet(store, fake_ots):
    store.record_poll("a", content="x")
    assert store.unanchored_polls("a") == 1

    stamp(store, ["a"])
    assert store.unanchored_polls("a") == 0

    store.record_poll("a", content="y")
    assert store.unanchored_polls("a") == 1


# -- tamper detection ------------------------------------------------------

def test_verify_refuses_a_manifest_edited_after_stamping(store, fake_ots):
    store.record_poll("a", content="x")
    result = stamp(store, ["a"])

    path = os.path.join(store.root, result["manifest_ref"])
    with open(path, "wb") as handle:
        handle.write(b'{"tampered":true}')

    outcome = anchor_module.verify(store, result["manifest_ref"])

    assert outcome["ok"] is False
    assert "does not match" in outcome["reason"]
