"""Tests for the read-only, deterministic public export boundary."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from kibitzr_archive.anchor import OTS_MAGIC, build_manifest
from kibitzr_archive.public_export import _validate_output, build_export
from kibitzr_archive.store import ArchiveStore, sha256_hex


CHECK = "Contracts Finder — recent awards"


def _release(amount=100, supplier="Supplier One", title="Bridge maintenance"):
    return json.dumps({
        "releases": [{
            "ocid": "ocds-test-1",
            "id": "ocds-test-1-award",
            "tag": ["award"],
            "date": "2026-01-02T12:00:00+00:00",
            "publishedDate": "2026-01-01T12:00:00+00:00",
            "buyer": {"id": "buyer-1", "name": "Test Buyer"},
            "tender": {
                "title": title,
                "classification": {"scheme": "CPV", "id": "45000000", "description": "Construction work"},
            },
            "awards": [{
                "id": "award-1",
                "date": "2026-01-02T12:00:00+00:00",
                "status": "active",
                "value": {"amount": amount, "currency": "GBP"},
                "suppliers": [{"id": "supplier-1", "name": supplier}],
                "contractPeriod": {"startDate": "2026-02-01", "endDate": "2027-02-01"},
                "documents": [{"url": "https://example.invalid/notice/1"}],
            }],
        }]
    }, sort_keys=True).encode("utf-8")


def _observe(store, raw, when, normalised=None):
    poll = store.record_poll(CHECK, url="https://example.invalid/feed",
                             content=raw, polled_at=when)
    if normalised is not None:
        store.record_normalisation(CHECK, normalised, transform_conf=["json"],
                                   poll_id=poll.poll_id, recorded_at=when)
    return poll


def _fake_proof(manifest_bytes):
    return OTS_MAGIC + b"\x01\x08" + hashlib.sha256(manifest_bytes).digest()


def _anchor(store, status="pending"):
    manifest, raw = build_manifest(
        store, store.check_names(), created_at="2026-01-03T00:00:00+00:00")
    root = Path(store.root) / "anchors"
    root.mkdir(exist_ok=True)
    ref = "anchors/2026-01-03T00-00-00+00-00.json"
    (Path(store.root) / ref).write_bytes(raw)
    (Path(store.root) / (ref + ".ots")).write_bytes(_fake_proof(raw))
    for entry in manifest["checks"]:
        store.record_anchor(entry, "opentimestamps", ref, sha256_hex(raw),
                            proof_ref=ref + ".ots", status=status,
                            anchored_at=manifest["created_at"])


def _build(store, tmp_path, name="export"):
    return build_export(store.root, tmp_path / name)


def test_unchanged_archive_exports_identically_and_is_stably_ordered(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    first = _release()
    _observe(store, first, "2026-01-01T12:00:00+00:00", first)
    second = _release(amount=125)
    _observe(store, second, "2026-01-02T12:00:00+00:00", second)

    left = _build(store, tmp_path, "left")
    right = _build(store, tmp_path, "right")
    left_files = sorted(path.relative_to(left).as_posix() for path in left.rglob("*.json"))
    right_files = sorted(path.relative_to(right).as_posix() for path in right.rglob("*.json"))
    assert left_files == right_files
    assert [path.read_bytes() for path in sorted(left.rglob("*.json"))] == [
        path.read_bytes() for path in sorted(right.rglob("*.json"))]

    for filename, key in (("sources.json", "sources"),
                          ("records.json", "records"),
                          ("changes.json", "changes")):
        values = json.loads((left / filename).read_text())[key]
        expected = sorted(
            values,
            key=(lambda item: item["id"])
            if key != "changes" else
            (lambda item: (item["first_observed_at"], item["id"])),
        )
        assert values == expected
    _validate_output(left)


def test_export_references_only_existing_observations_and_preserves_hashes(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    raw = _release()
    _observe(store, raw, "2026-01-01T12:00:00+00:00", raw)
    output = _build(store, tmp_path)
    observations = json.loads(
        (output / "observations/contracts-finder-recent-awards.json").read_text()
    )["observations"]
    observation_ids = {item["id"] for item in observations}
    records = json.loads((output / "records.json").read_text())["records"]
    changes = json.loads((output / "changes.json").read_text())["changes"]

    assert all(ref in observation_ids for record in records
               for ref in record["evidence"]["observation_ids"])
    assert all(change["observation_id"] in observation_ids for change in changes)
    for observation in observations:
        digest = observation["capture"]["content_sha256"]
        assert digest == sha256_hex(store.get_blob(digest))


def test_manifest_chain_heads_match_archive_and_observed_change_times_are_distinct(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    before = _release()
    _observe(store, before, "2026-01-01T12:00:00+00:00", before)
    after = _release(amount=200)
    _observe(store, after, "2026-01-02T12:00:00+00:00", after)
    output = _build(store, tmp_path)
    manifest = json.loads((output / "manifest.json").read_text())
    head = next(item for item in manifest["per_target_chain_heads"]
                if item["target_id"] == "contracts-finder-recent-awards")
    assert head["poll"] == store.head(CHECK)
    assert head["normalisation"] == store.normalisation_head(CHECK)
    assert head["annotation"] == store.annotation_head() or head["annotation"] == "0" * 64

    changes = json.loads((output / "changes.json").read_text())["changes"]
    value_change = next(item for item in changes
                        if item["event_type"] == "value_changed")
    assert value_change["first_observed_at"] == "2026-01-02T12:00:00+00:00"
    assert "changed_at" not in value_change
    assert value_change["comparison"]["mode"] == "structured_field"


def test_pending_anchor_is_never_exported_as_bitcoin_backed(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    raw = _release()
    _observe(store, raw, "2026-01-01T12:00:00+00:00", raw)
    _anchor(store, "pending")
    output = _build(store, tmp_path)
    observation = json.loads(
        (output / "observations/contracts-finder-recent-awards.json").read_text()
    )["observations"][0]
    assert observation["anchor"]["status"] == "pending"
    assert observation["anchor"]["status"] != "bitcoin-backed"


def test_unanchored_observation_is_awaiting_coverage_not_archive_damage(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    raw = _release()
    _observe(store, raw, "2026-01-01T12:00:00+00:00", raw)
    output = _build(store, tmp_path)
    observation = json.loads(
        (output / "observations/contracts-finder-recent-awards.json").read_text()
    )["observations"][0]
    assert observation["anchor"] == {
        "status": "none", "coverage": "awaiting-coverage"}


def test_export_does_not_write_archive(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    raw = _release()
    _observe(store, raw, "2026-01-01T12:00:00+00:00", raw)
    archive_files = {
        path.relative_to(Path(store.root)).as_posix(): path.read_bytes()
        for path in Path(store.root).rglob("*") if path.is_file()
    }
    _build(store, tmp_path)
    after_files = {
        path.relative_to(Path(store.root)).as_posix(): path.read_bytes()
        for path in Path(store.root).rglob("*") if path.is_file()
    }
    assert archive_files == after_files


def test_failed_export_leaves_previous_published_export_untouched(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    raw = _release()
    _observe(store, raw, "2026-01-01T12:00:00+00:00", raw)
    output = _build(store, tmp_path)
    before = (output / "manifest.json").read_bytes()

    digest = sha256_hex(raw)
    with gzip.open(Path(store.root) / "blobs" / digest[:2] / f"{digest}.gz", "wb") as handle:
        handle.write(b"corrupted retained bytes")
    with pytest.raises(ValueError, match="hashes to"):
        _build(store, tmp_path)
    assert output.is_dir()
    assert (output / "manifest.json").read_bytes() == before


def test_public_browser_uses_text_nodes_and_does_not_interpolate_html(tmp_path):
    del tmp_path  # keep this test independent of the archive fixture
    browser = Path(__file__).resolve().parents[3] / "public-browser-prototype" / "app.js"
    source = browser.read_text(encoding="utf-8")
    assert "innerHTML" not in source
    assert "createTextNode" in source
    assert "textContent" in source
