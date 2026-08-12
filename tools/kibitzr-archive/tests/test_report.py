"""Adversarial tests for the static evidence browser."""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
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
    assert '<script src="assets/theme.js"></script>' in html
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


def test_every_html_page_has_accessible_theme_selector_and_correct_assets(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    second, _ = _observe(store, "Check", b"two", "two")
    output, index = _generate(tmp_path, store, _config(tmp_path))
    detail = _detail(output, second.poll_id)

    for html in (index, detail):
        assert '<label for="theme-select">Theme</label>' in html
        assert 'id="theme-select" data-theme-selector' in html
        assert '<option value="system">System default</option>' in html
        assert '<option value="light">Light</option>' in html
        assert '<option value="dark">Dark</option>' in html
    assert 'src="assets/theme.js"' in index
    assert 'href="assets/report.css"' in index
    assert 'src="../assets/theme.js"' in detail
    assert 'href="../assets/report.css"' in detail


def test_csp_allows_only_local_external_script_and_has_no_inline_handlers(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    output, html = _generate(tmp_path, store)

    assert "script-src 'self'" in html
    assert "'unsafe-inline'" not in html
    assert "'unsafe-eval'" not in html
    assert "<script>" not in html
    assert "onclick=" not in html and "onchange=" not in html
    assert html.count("<script ") == 1
    assert (output / "assets" / "theme.js").is_file()


def _run_theme_script(tmp_path, stored="system", read_fails=False,
                      write_fails=False, change=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable for the dependency-free theme-script test")
    script_path = tmp_path / "theme.js"
    script_path.write_text(report.THEME_JS, encoding="utf-8")
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const options = JSON.parse(process.argv[1]);
const attributes = {};
const selectors = [makeSelector(), makeSelector()];
let ready;
let saved = options.stored;
function makeSelector() {
  return {value: '', listener: null, addEventListener: function (name, fn) { this.listener = fn; }};
}
global.window = {localStorage: {
  getItem: function () { if (options.readFails) throw new Error('read denied'); return saved; },
  setItem: function (_key, value) { if (options.writeFails) throw new Error('write denied'); saved = value; }
}};
global.document = {
  documentElement: {
    setAttribute: function (key, value) { attributes[key] = value; },
    removeAttribute: function (key) { delete attributes[key]; }
  },
  querySelectorAll: function () { return selectors; },
  addEventListener: function (name, fn) { if (name === 'DOMContentLoaded') ready = fn; }
};
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));
ready();
if (options.change) selectors[0].listener({target: {value: options.change}});
process.stdout.write(JSON.stringify({theme: attributes['data-theme'] || null,
  selectors: selectors.map(x => x.value), saved: saved}));
"""
    completed = subprocess.run(
        [node, "-e", harness, json.dumps({
            "stored": stored, "readFails": read_fails,
            "writeFails": write_fails, "change": change,
        }), str(script_path)], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


@pytest.mark.parametrize(("stored", "expected"), [
    ("system", None), ("light", "light"), ("dark", "dark"),
    ("unexpected", None), ("<script>", None), (None, None),
])
def test_theme_script_accepts_only_supported_stored_values(tmp_path, stored, expected):
    state = _run_theme_script(tmp_path, stored=stored)
    assert state["theme"] == expected
    assert state["selectors"] == [expected or "system"] * 2


def test_theme_script_survives_storage_read_failure(tmp_path):
    state = _run_theme_script(tmp_path, stored="dark", read_fails=True)
    assert state["theme"] is None
    assert state["selectors"] == ["system", "system"]


@pytest.mark.parametrize(("choice", "expected"), [
    ("light", "light"), ("dark", "dark"), ("system", None),
])
def test_theme_choice_updates_root_all_selectors_and_survives_write_failure(
        tmp_path, choice, expected):
    state = _run_theme_script(
        tmp_path, stored="system", write_fails=True, change=choice)
    assert state["theme"] == expected
    assert state["selectors"] == [choice, choice]


def test_css_has_explicit_palettes_focus_diff_text_and_light_print(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"before", "before")
    second, _ = _observe(store, "Check", b"after", "after")
    output, _html = _generate(tmp_path, store, _config(tmp_path))
    css = (output / "assets" / "report.css").read_text(encoding="utf-8")
    detail = _detail(output, second.poll_id)

    for variable in ("--page-background", "--panel-background", "--text-primary",
                     "--text-muted", "--border", "--link", "--link-visited",
                     "--success", "--warning", "--failure", "--code-background",
                     "--diff-added-background", "--diff-added-text",
                     "--diff-removed-background", "--diff-removed-text", "--focus-ring"):
        assert variable in css
    assert ':root[data-theme="dark"]' in css
    assert "prefers-color-scheme:dark" in css
    assert "@media print" in css and "color-scheme:light" in css
    assert ":focus-visible" in css
    assert "- before" in detail and "+ after" in detail


def test_publication_manifest_is_safe_derived_build_metadata(tmp_path, monkeypatch):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    _observe(store, "Check", b"two", "two")
    monkeypatch.setattr(report, "_now", lambda: "2026-08-05T12:00:00+00:00")
    output, _ = _generate(tmp_path, store, _config(tmp_path))
    manifest = json.loads((output / "publication-manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["report_kind"] == "derived-evidence-archive-report"
    assert manifest["verification_result"] == "verified"
    assert manifest["poll_count"] == 2
    assert manifest["target_count"] == 1
    assert manifest["published_change_page_count"] == 1
    assert manifest["published_response_count"] == 2
    encoded = json.dumps(manifest)
    assert str(tmp_path) not in encoded
    assert "/home/" not in encoded and "file://" not in encoded
    assert "Check" not in encoded


def test_validator_checks_all_local_targets_fragments_and_machine_paths(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", b"one", "one")
    output, _ = _generate(tmp_path, store)
    result = report.validate_report(output)
    assert result["html_pages"] == 1
    assert result["references_checked"] >= 3

    (output / "index.html").write_text(
        '<html><body><a href="missing.html">missing</a></body></html>', encoding="utf-8")
    with pytest.raises(ValueError, match="missing target"):
        report.validate_report(output)
    (output / "index.html").write_text(
        '<html><body><p>/home/developer/private</p></body></html>', encoding="utf-8")
    with pytest.raises(ValueError, match="development-machine path"):
        report.validate_report(output)
    (output / "index.html").write_text(
        '<html><body><script src="https://cdn.invalid/theme.js"></script></body></html>',
        encoding="utf-8")
    with pytest.raises(ValueError, match="remote asset"):
        report.validate_report(output)


def test_all_raw_download_links_are_inert_and_resolve(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    attack = b"<html><script>alert(1)</script></html>"
    _observe(store, "Check", attack, attack.decode())
    second, _ = _observe(store, "Check", b"safe", "safe")
    output, _ = _generate(tmp_path, store, _config(tmp_path, rules=[{"changes": "verbose"}]))
    detail = _detail(output, second.poll_id)

    assert detail.count(" download ") >= 2
    for path in (output / "responses").iterdir():
        assert path.suffix == ".txt"
        assert path.is_file()
    assert "<script>alert" not in detail


def test_theme_and_publication_files_do_not_modify_archive_or_verification(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    _observe(store, "Check", "£1 — before".encode(), "£1 — before")
    _observe(store, "Check", "£2 — after".encode(), "£2 — after")
    before_files = {p: (p.stat().st_mtime_ns, p.read_bytes())
                    for p in Path(store.root).rglob("*") if p.is_file()}
    before_heads = (store.combined_head("Check"), report._verification_result(store))
    output, html = _generate(tmp_path, store, _config(tmp_path))
    after_files = {p: (p.stat().st_mtime_ns, p.read_bytes())
                   for p in Path(store.root).rglob("*") if p.is_file()}

    assert before_files == after_files
    assert before_heads == (store.combined_head("Check"), report._verification_result(store))
    detail = _detail(output, 2)
    assert "£" in detail and "—" in detail
    assert "Â£" not in html + detail and "â€”" not in html + detail
    assert report.validate_report(output)["html_pages"] == 2
