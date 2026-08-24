"""Adversarial tests for the independent public projection verifier."""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from kibitzr_archive.public_export import build_export
from kibitzr_archive.store import ArchiveStore


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy" / "verify_public_export_independently.py"
SPEC = importlib.util.spec_from_file_location("independent_public_verifier", SCRIPT)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def _release(amount=100, title="Bridge maintenance", ocid="ocds-verifier-test-1"):
    return json.dumps({
        "releases": [{
            "ocid": ocid,
            "id": "ocds-verifier-test-1-award",
            "tag": ["award"],
            "date": "2026-01-02T12:00:00+00:00",
            "publishedDate": "2026-01-01T12:00:00+00:00",
            "buyer": {"id": "buyer-1", "name": "Test Buyer"},
            "tender": {"title": title,
                       "classification": {"scheme": "CPV", "id": "45000000",
                                           "description": "Construction work"}},
            "awards": [{"id": "award-1", "date": "2026-01-02T12:00:00+00:00",
                        "status": "active",
                        "value": {"amount": amount, "currency": "GBP"},
                        "suppliers": [{"id": "supplier-1", "name": "Supplier One"}],
                        "documents": [{"url": "https://example.invalid/notice/1"}]},
            ],
        }]
    }, sort_keys=True).encode()


def _fixture(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    second = json.loads(_release(200))
    second["releases"].append(
        json.loads(_release(50, title="Second bridge", ocid="ocds-verifier-test-2"))
        ["releases"][0])
    for body, when in ((_release(100), "2026-01-01T12:00:00+00:00"),
                       (json.dumps(second).encode(), "2026-01-02T12:00:00+00:00")):
        store.record_poll("Contracts Finder — recent awards",
                          url="https://example.invalid/feed", content=body,
                          polled_at=when)
    output = build_export(store.root, tmp_path / "export")
    assert VERIFIER.verify_export(store.root, output) == 0
    return output


def _historical_fixture(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    store.record_poll("Contracts Finder — recent awards",
                      url="https://example.invalid/feed", content=_release(100),
                      polled_at="2026-01-01T12:00:00+00:00")
    store.record_poll("Contracts Finder — recent awards",
                      url="https://example.invalid/feed", content=_release(200),
                      polled_at="2026-01-02T12:00:00+00:00")
    snapshot = build_export(store.root, tmp_path / "snapshot")
    store.record_poll(
        "Contracts Finder — recent awards", url="https://example.invalid/feed",
        content=_release(300, title="Later bridge", ocid="ocds-later"),
        polled_at="2026-01-03T12:00:00+00:00")
    current = build_export(store.root, tmp_path / "current")
    return store.root, snapshot, current


def _read(output, relative):
    return json.loads((Path(output) / relative).read_text(encoding="utf-8"))


def _write(output, relative, value):
    path = Path(output) / relative
    path.write_bytes(VERIFIER.public_json(value))


def _resign(output):
    """Update superficial file hashes and export identity after a mutation."""
    output = Path(output)
    manifest = _read(output, "manifest.json")
    files = []
    for path in sorted(output.rglob("*.json")):
        relative = path.relative_to(output).as_posix()
        if relative == "manifest.json":
            continue
        data = path.read_bytes()
        files.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(),
                      "bytes": len(data)})
    manifest["files"] = files
    basis = {"schema_version": manifest["schema_version"],
             "generated_at": manifest["generated_at"], "files": files,
             "chain_heads": [{key: item[key] for key in
                              ("poll", "normalisation", "annotation")}
                             for item in manifest["per_target_chain_heads"]]}
    manifest["export_id"] = "export-" + hashlib.sha256(
        VERIFIER.public_json(basis)).hexdigest()[:32]
    _write(output, "manifest.json", manifest)


def _remove_record(output, record_id):
    records = _read(output, "records.json")
    records["records"] = [item for item in records["records"]
                           if item["id"] != record_id]
    _write(output, "records.json", records)
    (Path(output) / "records" / f"{VERIFIER.record_file_id(record_id)}.json").unlink()


def _one_record(output):
    return _read(output, "records.json")["records"][0]


def _historical_assertion(tmp_path, mutate=None):
    archive, snapshot, current = _historical_fixture(tmp_path)
    if mutate:
        mutate(snapshot, current, archive)
    assert VERIFIER.verify_historical_export(archive, snapshot) == 1


def test_historical_mode_accepts_authenticated_append_only_prefix(tmp_path):
    archive, snapshot, _current = _historical_fixture(tmp_path)
    assert VERIFIER.verify_historical_export(archive, snapshot) == 0


def test_current_mode_rejects_historical_snapshot_against_live_archive(tmp_path):
    archive, snapshot, _current = _historical_fixture(tmp_path)
    assert VERIFIER.verify_export(archive, snapshot) == 1


def test_current_mode_accepts_entire_live_archive_export(tmp_path):
    archive, _snapshot, current = _historical_fixture(tmp_path)
    assert VERIFIER.verify_export(archive, current) == 0

def test_historical_cut_enables_legacy_control_events_for_known_export(tmp_path):
    archive, snapshot, _current = _historical_fixture(tmp_path)
    manifest = _read(snapshot, "manifest.json")
    manifest["export_id"] = VERIFIER.LEGACY_CONTROL_EVENT_EXPORT_ID

    errors = []
    cut = VERIFIER.derive_historical_cut(
        VERIFIER.Archive(archive), manifest, errors)

    assert errors == []
    assert cut is not None
    assert cut.legacy_control_events is True


def test_historical_cut_does_not_enable_legacy_control_events_normally(tmp_path):
    archive, snapshot, _current = _historical_fixture(tmp_path)
    manifest = _read(snapshot, "manifest.json")

    assert manifest["export_id"] != VERIFIER.LEGACY_CONTROL_EVENT_EXPORT_ID

    errors = []
    cut = VERIFIER.derive_historical_cut(
        VERIFIER.Archive(archive), manifest, errors)

    assert errors == []
    assert cut is not None
    assert cut.legacy_control_events is False


def test_forged_legacy_export_id_does_not_bypass_verification(tmp_path):
    archive, snapshot, _current = _historical_fixture(tmp_path)
    manifest = _read(snapshot, "manifest.json")
    manifest["export_id"] = VERIFIER.LEGACY_CONTROL_EVENT_EXPORT_ID
    _write(snapshot, "manifest.json", manifest)

    assert VERIFIER.verify_historical_export(archive, snapshot) == 1


def test_historical_rejects_forged_cut_metadata(tmp_path):
    def mutate(snapshot, current, _archive):
        manifest = _read(snapshot, "manifest.json")
        later_head = _read(current, "manifest.json")["per_target_chain_heads"][0]["poll"]
        manifest["per_target_chain_heads"][0]["poll"] = later_head
        _write(snapshot, "manifest.json", manifest)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_fake_chain_head(tmp_path):
    def mutate(snapshot, _current, _archive):
        manifest = _read(snapshot, "manifest.json")
        manifest["per_target_chain_heads"][0]["poll"] = hashlib.sha256(
            b"fake chain head").hexdigest()
        _write(snapshot, "manifest.json", manifest)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_head_not_present_in_live_archive(tmp_path):
    def mutate(snapshot, _current, _archive):
        manifest = _read(snapshot, "manifest.json")
        manifest["per_target_chain_heads"][0]["poll"] = "a" * 64
        _write(snapshot, "manifest.json", manifest)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_missing_intermediate_poll_rows(tmp_path):
    def mutate(_snapshot, _current, archive):
        with sqlite3.connect(Path(archive) / "polls.db") as conn:
            conn.execute("DELETE FROM poll WHERE id = 1")

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_broken_chain_continuity(tmp_path):
    def mutate(_snapshot, _current, archive):
        with sqlite3.connect(Path(archive) / "polls.db") as conn:
            conn.execute("UPDATE poll SET prev_hash = ? WHERE id = 2",
                         ("0" * 64,))

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_rewritten_later_append(tmp_path):
    def mutate(_snapshot, _current, archive):
        # Poll 3 is after the authenticated cut.  It is still part of the
        # append-only evidence that historical mode must validate as a suffix.
        with sqlite3.connect(Path(archive) / "polls.db") as conn:
            conn.execute("UPDATE poll SET polled_at = ? WHERE id = 3",
                         ("2026-01-04T12:00:00+00:00",))

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_chain_head_mismatch(tmp_path):
    def mutate(snapshot, _current, _archive):
        manifest = _read(snapshot, "manifest.json")
        manifest["per_target_chain_heads"][0]["annotation"] = \
            manifest["per_target_chain_heads"][0]["poll"]
        _write(snapshot, "manifest.json", manifest)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_observation_tail_not_matching_authenticated_head(
        tmp_path):
    def mutate(snapshot, _current, _archive):
        observations = _read(
            snapshot, "observations/contracts-finder-recent-awards.json")
        observations["observations"][-1]["id"] = \
            "contracts-finder-recent-awards:poll:1"
        _write(snapshot, "observations/contracts-finder-recent-awards.json",
               observations)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_omitted_eligible_observation_inside_cut(tmp_path):
    def mutate(snapshot, _current, _archive):
        observations = _read(
            snapshot, "observations/contracts-finder-recent-awards.json")
        # Keep the tail/head intact while removing an earlier eligible row.
        observations["observations"].pop(0)
        _write(snapshot, "observations/contracts-finder-recent-awards.json",
               observations)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_observation_after_authenticated_cut(tmp_path):
    def mutate(snapshot, current, _archive):
        observations = _read(
            snapshot, "observations/contracts-finder-recent-awards.json")
        later = _read(
            current, "observations/contracts-finder-recent-awards.json")
        observations["observations"].append(later["observations"][-1])
        _write(snapshot, "observations/contracts-finder-recent-awards.json",
               observations)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_snapshot_claiming_records_beyond_cut(tmp_path):
    def mutate(snapshot, current, _archive):
        records = _read(snapshot, "records.json")
        snapshot_ids = {item["id"] for item in records["records"]}
        later = next(item for item in _read(current, "records.json")["records"]
                     if item["id"] not in snapshot_ids)
        records["records"].append(later)
        _write(snapshot, "records.json", records)
        _write(snapshot, f"records/{VERIFIER.record_file_id(later['id'])}.json",
               later)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_cli_does_not_accept_an_arbitrary_cut_number(tmp_path):
    archive, snapshot, _current = _historical_fixture(tmp_path)
    with pytest.raises(SystemExit):
        VERIFIER.main(["--historical", "--cut-poll", "1",
                       str(archive), str(snapshot)])


def test_historical_rejects_snapshot_omitting_record_inside_cut(tmp_path):
    def mutate(snapshot, _current, _archive):
        record = _one_record(snapshot)
        _remove_record(snapshot, record["id"])
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_forged_event_inside_cut(tmp_path):
    def mutate(snapshot, _current, _archive):
        changes = _read(snapshot, "changes.json")
        changes["changes"][0]["first_observed_at"] = "1999-01-01T00:00:00+00:00"
        _write(snapshot, "changes.json", changes)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_altered_source_observation_relationship(tmp_path):
    def mutate(snapshot, _current, _archive):
        records = _read(snapshot, "records.json")
        records["records"][0]["evidence"]["observation_ids"] = [
            "contracts-finder-recent-awards:poll:999"]
        _write(snapshot, "records.json", records)
        record = records["records"][0]
        _write(snapshot, f"records/{VERIFIER.record_file_id(record['id'])}.json",
               record)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_historical_rejects_altered_entity_relationship_set(tmp_path):
    def mutate(snapshot, _current, _archive):
        entities = _read(snapshot, "entities.json")
        entities["entities"][0]["record_ids"] = []
        _write(snapshot, "entities.json", entities)
        _resign(snapshot)

    _historical_assertion(tmp_path, mutate)


def test_delete_all_records_and_resign_still_fails_completeness(tmp_path):
    output = _fixture(tmp_path)
    records = _read(output, "records.json")
    for record in list(records["records"]):
        _remove_record(output, record["id"])
    records["records"] = []
    _write(output, "records.json", records)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_delete_one_record_and_resign_fails(tmp_path):
    output = _fixture(tmp_path)
    _remove_record(output, _one_record(output)["id"])
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_delete_one_observation_and_resign_fails(tmp_path):
    output = _fixture(tmp_path)
    observations = _read(output, "observations/contracts-finder-recent-awards.json")
    observations["observations"].pop()
    _write(output, "observations/contracts-finder-recent-awards.json", observations)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_delete_one_entity_and_resign_fails(tmp_path):
    output = _fixture(tmp_path)
    entities = _read(output, "entities.json")
    entities["entities"].pop()
    _write(output, "entities.json", entities)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_delete_one_record_file_and_resign_fails(tmp_path):
    output = _fixture(tmp_path)
    record = _one_record(output)
    (Path(output) / "records" / f"{VERIFIER.record_file_id(record['id'])}.json").unlink()
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_duplicate_record_while_deleting_another_fails(tmp_path):
    output = _fixture(tmp_path)
    records = _read(output, "records.json")
    removed = records["records"].pop()
    records["records"].append(records["records"][0])
    _write(output, "records.json", records)
    (Path(output) / "records" / f"{VERIFIER.record_file_id(removed['id'])}.json").unlink()
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_fabricated_structurally_valid_record_fails(tmp_path):
    output = _fixture(tmp_path)
    records = _read(output, "records.json")
    fabricated = json.loads(json.dumps(records["records"][0]))
    fabricated["id"] = "record:contracts-finder-recent-awards:fabricated"
    records["records"].append(fabricated)
    _write(output, "records.json", records)
    _write(output, f"records/{VERIFIER.record_file_id(fabricated['id'])}.json", fabricated)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_altered_chain_head_fails_after_export_identity_is_resigned(tmp_path):
    output = _fixture(tmp_path)
    manifest = _read(output, "manifest.json")
    manifest["per_target_chain_heads"][0]["poll"] = "f" * 64
    _write(output, "manifest.json", manifest)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_altered_export_id_fails_without_stale_file_hashes(tmp_path):
    output = _fixture(tmp_path)
    manifest = _read(output, "manifest.json")
    manifest["export_id"] = "export-" + "0" * 32
    _write(output, "manifest.json", manifest)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_internally_self_consistent_truncation_still_fails(tmp_path):
    output = _fixture(tmp_path)
    record = _one_record(output)
    _remove_record(output, record["id"])
    entities = _read(output, "entities.json")
    entities["entities"] = [entity for entity in entities["entities"]
                             if record["buyer"] is None or
                             entity["id"] != record["buyer"]["id"]]
    _write(output, "entities.json", entities)
    observations = _read(output, "observations/contracts-finder-recent-awards.json")
    observations["observations"] = observations["observations"][:-1]
    _write(output, "observations/contracts-finder-recent-awards.json", observations)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def _value_event(output):
    changes = _read(output, "changes.json")
    return changes, next(item for item in changes["changes"]
                        if item["event_type"] == "value_changed")


def _mutate_event(tmp_path, mutate):
    output = _fixture(tmp_path)
    changes, selected = _value_event(output)
    mutate(selected)
    _write(output, "changes.json", changes)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_forged_event_timestamp_fails(tmp_path):
    _mutate_event(tmp_path, lambda event: event.update(
        first_observed_at="1999-01-01T00:00:00+00:00"))


def test_forged_event_title_fails(tmp_path):
    _mutate_event(tmp_path, lambda event: event.update(title="Fabricated title"))


def test_forged_event_old_value_fails(tmp_path):
    _mutate_event(tmp_path, lambda event: event["comparison"].update(
        old={"amount": -1, "currency": "GBP"}))


def test_forged_event_new_value_fails(tmp_path):
    _mutate_event(tmp_path, lambda event: event["comparison"].update(
        new={"amount": 999999, "currency": "GBP"}))


def test_forged_event_id_fails(tmp_path):
    _mutate_event(tmp_path, lambda event: event.update(id="event:" + "0" * 24))


def test_forged_previous_observation_fails(tmp_path):
    _mutate_event(tmp_path, lambda event: event.update(
        previous_observation_id="contracts-finder-recent-awards:poll:999"))


def test_wrong_event_type_fails(tmp_path):
    _mutate_event(tmp_path, lambda event: event.update(event_type="status_changed"))


def test_missing_event_fails(tmp_path):
    output = _fixture(tmp_path)
    changes = _read(output, "changes.json")
    changes["changes"] = changes["changes"][1:]
    _write(output, "changes.json", changes)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_extra_fabricated_event_fails(tmp_path):
    output = _fixture(tmp_path)
    changes = _read(output, "changes.json")
    fabricated = json.loads(json.dumps(changes["changes"][0]))
    fabricated["id"] = "event:" + "1" * 24
    changes["changes"].append(fabricated)
    _write(output, "changes.json", changes)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_duplicate_event_fails(tmp_path):
    output = _fixture(tmp_path)
    changes = _read(output, "changes.json")
    changes["changes"].append(changes["changes"][0])
    _write(output, "changes.json", changes)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1


def test_control_source_event_is_rejected_from_bi_stream(tmp_path):
    output = _fixture(tmp_path)
    changes = _read(output, "changes.json")
    fabricated = json.loads(json.dumps(changes["changes"][0]))
    fabricated["target_id"] = "control-collector-liveness"
    fabricated["id"] = "event:" + "2" * 24
    changes["changes"].append(fabricated)
    _write(output, "changes.json", changes)
    _resign(output)
    assert VERIFIER.verify_export(tmp_path / "archive", output) == 1
