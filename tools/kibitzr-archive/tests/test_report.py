"""Adversarial tests for the static evidence browser."""

import gzip
import hashlib
import os
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from kibitzr_archive import report
from kibitzr_archive.anchor import OTS_MAGIC, build_manifest
from kibitzr_archive.cli import extend_cli
from kibitzr_archive.store import ArchiveStore, sha256_hex


def _cli():
    @click.group()
    def root():
        pass

    extend_cli(root)
    return root


def _config(tmp_path, name="Check", rules=None, url="https://example.invalid"):
    rules = rules or ["text", {"changes": "verbose"}]
    rendered = []
    for rule in rules:
        if isinstance(rule, str):
            rendered.append(f"      - {rule}")
        else:
            key, value = next(iter(rule.items()))
            rendered.append(f"      - {key}: {value}")
    path = tmp_path / "kibitzr.yml"
    path.write_text(
        "checks:\n"
        f"  - name: {name!r}\n"
        f"    url: {url}\n"
        "    archive: true\n"
        "    transform:\n" + "\n".join(rendered) + "\n",
        encoding="utf-8",
    )
    return path


def _observe(store, name, raw, normalised=None, ok=True, **kwargs):
    poll = store.record_poll(name, url="https://example.invalid", ok=ok,
                             content=raw if ok else None,
                             error=None if ok else "network failed", **kwargs)
    norm = None
    if ok and normalised is not None:
        norm = store.record_normalisation(
            name, normalised, transform_conf=["text", {"changes": "verbose"}],
            poll_id=poll.poll_id, recorded_at=poll.polled_at)
    return poll, norm


def _generate(tmp_path, store, config=None, output_name="report"):
    output = tmp_path / output_name
    report.write(store, output, config_path=config)
    return output, (output / "index.html").read_text(encoding="utf-8")


def _detail(output, poll_id):
    return (output / "changes" / f"poll-{poll_id:06d}.html").read_text(encoding="utf-8")


def _fake_proof(manifest_bytes):
    # committed_digest only needs the detached proof header, version, hash op,
    # and covered file digest. Timestamp attestations follow these bytes.
    return OTS_MAGIC + b"\x01\x08" + hashlib.sha256(manifest_bytes).digest()


def _anchor(store, status):
    manifest, raw = build_manifest(store, store.check_names(), created_at="2026-08-05T12:00:00+00:00")
    root = Path(store.root) / "anchors"
    root.mkdir(exist_ok=True)
    ref = "anchors/2026-08-05T12-00-00+00-00.json"
    (Path(store.root) / ref).write_bytes(raw)
    (Path(store.root) / (ref + ".ots")).write_bytes(_fake_proof(raw))
    for entry in manifest["checks"]:
        store.record_anchor(entry, "opentimestamps", ref, sha256_hex(raw),
                            proof_ref=ref + ".ots", status=status,
                            anchored_at=manifest["created_at"])


def test_static_directory_escapes_content_and_uses_only_local_assets(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check <unsafe>", b"<script>alert(1)</script>", "safe")
    output, html = _generate(tmp_path, store)

    assert (output / "assets" / "report.css").is_file()
    assert "Check &lt;unsafe&gt;" in html
    assert "Check <unsafe>" not in html
    assert "script src=" not in html
    assert "https://" not in html
    assert str(tmp_path) not in html


def test_spelling_change_gets_small_hash_verified_diff(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"The colour is blue", "The colour is blue")
    after, _ = _observe(store, "Check", b"The color is blue", "The color is blue")
    output, html = _generate(tmp_path, store, _config(tmp_path))
    detail = _detail(output, after.poll_id)

    assert "View change" in html
    assert "- The colour is blue" in detail
    assert "+ The color is blue" in detail
    assert "Verified document diff unavailable" not in detail


@pytest.mark.parametrize(("old", "new", "expected"), [
    ("Old Supplier", "New Supplier", "Supplier changed: ['Old Supplier'] → ['New Supplier']"),
    (1200000, 12000000, "Contract value changed: 1200000 → 12000000"),
])
def test_ocds_supplier_and_value_changes_are_explicit(tmp_path, old, new, expected):
    store = ArchiveStore(str(tmp_path / "archive"))
    def release(value):
        supplier = value if isinstance(value, str) else "Supplier"
        amount = value if isinstance(value, int) else 1200000
        return ('{"releases":[{"ocid":"ocds-1","awards":[{"suppliers":[{"name":"%s"}],'
                '"value":{"amount":%s,"currency":"GBP"}}]}]}' % (supplier, amount))
    before, after = release(old), release(new)
    _observe(store, "Check", before.encode(), before)
    poll, _ = _observe(store, "Check", after.encode(), after)
    output, _html = _generate(tmp_path, store, _config(tmp_path))

    assert expected in _detail(output, poll.poll_id)


def test_raw_nonce_churn_does_not_create_document_change_page(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b'<main nonce="one">same</main>', "same")
    poll, _ = _observe(store, "Check", b'<main nonce="two">same</main>', "same")
    output, html = _generate(tmp_path, store, _config(tmp_path))

    assert "View raw response" in html
    assert "View change" not in html
    assert not (output / "changes" / f"poll-{poll.poll_id:06d}.html").exists()


def test_initial_capture_and_failed_poll_have_no_change_claim(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"first", "first")
    _observe(store, "Check", None, None, ok=False)
    _output, html = _generate(tmp_path, store, _config(tmp_path))

    assert "Initial capture" in html
    assert "Failed" in html
    assert "View change" not in html


def test_changed_transform_suppresses_semantic_attribution(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    first = store.record_poll("Check", content=b"one")
    store.record_normalisation("Check", "one", transform_conf=["text"], poll_id=first.poll_id)
    second = store.record_poll("Check", content=b"two")
    store.record_normalisation("Check", "two", transform_conf=["strip"], poll_id=second.poll_id)
    output, html = _generate(tmp_path, store, _config(tmp_path))
    detail = _detail(output, second.poll_id)

    assert "View evidence" in html
    assert "Extraction rules changed between these observations" in detail
    assert "Readable diff" not in detail


def test_rederived_hash_mismatch_is_rejected(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "not what transform produces")
    second, _ = _observe(store, "Check", b"two", "also different")
    output, _ = _generate(tmp_path, store, _config(tmp_path))

    assert "re-derived before document hashes" in _detail(output, second.poll_id)
    assert "Readable diff" not in _detail(output, second.poll_id)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_raw_blob_cannot_bless_diff(tmp_path, damage):
    store = ArchiveStore(str(tmp_path / "archive"))
    first, _ = _observe(store, "Check", b"one", "one")
    second, _ = _observe(store, "Check", b"two", "two")
    path = Path(store.blob_path(first.content_sha256))
    if damage == "missing":
        path.unlink()
    else:
        with gzip.open(path, "wb") as fp:
            fp.write(b"substitute")
    output, html = _generate(tmp_path, store, _config(tmp_path))
    detail = _detail(output, second.poll_id)

    assert "View evidence" in html
    assert "Verified document diff unavailable" in detail
    assert "Readable diff" not in detail


def test_malicious_html_is_escaped_and_raw_is_inert_text(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    attack = '<script>alert(1)</script><img src=x onerror="steal()">'
    first = store.record_poll("Check", content=attack.encode())
    store.record_normalisation("Check", attack, transform_conf=[{"changes": "verbose"}], poll_id=first.poll_id)
    second = store.record_poll("Check", content=b"safe")
    store.record_normalisation("Check", "safe", transform_conf=[{"changes": "verbose"}], poll_id=second.poll_id)
    output, _ = _generate(tmp_path, store, _config(tmp_path, rules=[{"changes": "verbose"}]))
    detail = _detail(output, second.poll_id)
    raw_files = list((output / "responses").iterdir())

    assert "<script>alert" not in detail
    assert "&lt;script&gt;alert" in detail
    assert all(path.suffix == ".txt" for path in raw_files)
    assert all(".html" not in path.name for path in raw_files)
    assert "Content-Security-Policy" in detail


def test_unicode_and_stable_global_poll_filename(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    name = "Contracts — £ values"
    _observe(store, name, "£1".encode(), "£1")
    second, _ = _observe(store, name, "£2".encode(), "£2")
    output, html = _generate(tmp_path, store, _config(tmp_path, name=name))

    assert "Contracts — £ values" in html
    assert "â€”" not in html and "Â£" not in html
    assert (output / "changes" / f"poll-{second.poll_id:06d}.html").is_file()


@pytest.mark.parametrize(("status", "label"), [
    ("complete", "Bitcoin-attested"),
    ("pending", "Pending calendar attestation"),
    (None, "Unanchored"),
])
def test_anchor_states_render_distinctly(tmp_path, status, label):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    second, _ = _observe(store, "Check", b"two", "two")
    if status:
        _anchor(store, status)
    output, _ = _generate(tmp_path, store, _config(tmp_path))

    assert label in _detail(output, second.poll_id)


def test_correction_annotation_appears_even_when_recorded_later(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    first, _ = _observe(store, "Check", b"one", "one")
    second, _ = _observe(store, "Check", b"two", "two")
    store.record_annotation("correction", {"reason": "publisher corrected supplier"},
                            check_name="Check", subject_from=first.poll_id,
                            subject_to=second.poll_id,
                            recorded_at="2026-08-06T00:00:00+00:00")
    output, _ = _generate(tmp_path, store, _config(tmp_path))

    detail = _detail(output, second.poll_id)
    assert "correction" in detail
    assert "publisher corrected supplier" in detail


def test_generation_does_not_modify_archive_files(tmp_path, monkeypatch):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in Path(store.root).rglob("*") if p.is_file()}
    monkeypatch.setattr(report, "_now", lambda: "2026-08-05T12:00:00+00:00")
    _generate(tmp_path, store, _config(tmp_path), "first-report")
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in Path(store.root).rglob("*") if p.is_file()}

    assert before == after


def test_identical_input_produces_equivalent_evidence_content(tmp_path, monkeypatch):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    _observe(store, "Check", b"two", "two")
    monkeypatch.setattr(report, "_now", lambda: "2026-08-05T12:00:00+00:00")
    config = _config(tmp_path)
    first, _ = _generate(tmp_path, store, config, "report-one")
    second, _ = _generate(tmp_path, store, config, "report-two")
    snapshot = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    assert snapshot(first) == snapshot(second)


def test_failure_does_not_replace_previous_complete_report(tmp_path, monkeypatch):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    output, _ = _generate(tmp_path, store, _config(tmp_path))
    original = (output / "index.html").read_bytes()
    monkeypatch.setattr(report, "_render_index", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        report.write(store, output)
    assert (output / "index.html").read_bytes() == original


def test_output_inside_archive_is_refused(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")

    with pytest.raises(ValueError, match="outside the archive"):
        report.write(store, Path(store.root) / "report")


def test_cli_writes_directory_and_recent_polls_are_bounded(tmp_path):
    archive = tmp_path / "archive"
    store = ArchiveStore(str(archive))
    for value in range(105):
        store.record_poll("Check", content=str(value))
    output = tmp_path / "browser"
    result = CliRunner().invoke(_cli(), ["archive", "report", "--root", str(archive), "--output", str(output)])

    assert result.exit_code == 0, result.output
    html = (output / "index.html").read_text(encoding="utf-8")
    assert "Evidence browser written" in result.output
    assert "<td>105</td>" in html
    assert "<td>5</td>" not in html


def test_recent_polls_store_query_is_newest_first_and_bounded(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    for value in range(4):
        store.record_poll("check", content=str(value))
    assert [row["id"] for row in store.recent_polls(limit=2)] == [4, 3]
