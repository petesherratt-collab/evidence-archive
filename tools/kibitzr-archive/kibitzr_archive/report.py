"""Static, hash-backed evidence browser for an evidence archive.

The browser is derived material.  It never becomes an input to a chain and it
only displays reconstructed document content after reproducing the digest in
the normalisation log.
"""

from __future__ import annotations

import difflib
import hashlib
import html.parser
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import integrity
from .anchor import ANCHOR_DIR, ProofFormatError, committed_digest
from .hook import capture_index, transform_rules
from .store import BlobMismatch, sha256_hex, transform_id


CSS = """
:root{color-scheme:light;
--page-background:#f5f7f9;--panel-background:#fff;--text-primary:#17212b;
--text-muted:#526170;--border:#cbd4dc;--link:#075fa8;--link-visited:#65459b;
--success:#176b43;--warning:#765000;--failure:#9d2821;--code-background:#edf1f4;
--diff-added-background:#d7f3e2;--diff-added-text:#07552f;
--diff-removed-background:#ffe0dc;--diff-removed-text:#821b15;--focus-ring:#006fd6}
:root[data-theme="dark"]{color-scheme:dark;
--page-background:#10161c;--panel-background:#18212b;--text-primary:#edf2f6;
--text-muted:#b4bec8;--border:#465563;--link:#7fc4ff;--link-visited:#c8a9ff;
--success:#72d8a5;--warning:#f2c15c;--failure:#ff928a;--code-background:#222d37;
--diff-added-background:#123f2b;--diff-added-text:#a7efc8;
--diff-removed-background:#4b211e;--diff-removed-text:#ffbbb5;--focus-ring:#79c7ff}
@media(prefers-color-scheme:dark){:root:not([data-theme]){color-scheme:dark;
--page-background:#10161c;--panel-background:#18212b;--text-primary:#edf2f6;
--text-muted:#b4bec8;--border:#465563;--link:#7fc4ff;--link-visited:#c8a9ff;
--success:#72d8a5;--warning:#f2c15c;--failure:#ff928a;--code-background:#222d37;
--diff-added-background:#123f2b;--diff-added-text:#a7efc8;
--diff-removed-background:#4b211e;--diff-removed-text:#ffbbb5;--focus-ring:#79c7ff}}
*{box-sizing:border-box}body{margin:0;background:var(--page-background);color:var(--text-primary);
font:14px/1.5 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:28px}
h1{margin:0;font-size:26px}h2{font-size:18px;margin:0 0 12px}h3{font-size:15px}
.report-tools{display:flex;justify-content:flex-end;margin-bottom:14px}.theme-control{display:flex;
align-items:center;gap:8px}.theme-control label{font-weight:600}.theme-control select{font:inherit;
color:var(--text-primary);background:var(--panel-background);border:1px solid var(--border);
border-radius:6px;padding:6px 30px 6px 9px}.sub,.muted{color:var(--text-muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card,section{background:var(--panel-background);border:1px solid var(--border);border-radius:9px}
.card{padding:15px}.card span{display:block;color:var(--text-muted)}.card strong{font-size:22px}
.good{color:var(--success)}.warn{color:var(--warning)}.bad{color:var(--failure)}
section{padding:18px;margin-top:16px;
overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;
vertical-align:top;padding:9px;border-bottom:1px solid var(--border)}th{color:var(--text-muted)}
code,.hash{font-family:ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}
code,pre{background:var(--code-background)}pre{padding:10px;border-radius:6px}
.pill{border-radius:99px;padding:2px 7px;background:var(--code-background);white-space:nowrap}
.pill.complete{background:var(--diff-added-background);color:var(--diff-added-text)}
.pill.pending{background:var(--code-background);color:var(--warning)}
.pill.unanchored,.pill.failed{background:var(--diff-removed-background);color:var(--diff-removed-text)}
.diff{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,monospace}
.diff .add{background:var(--diff-added-background);color:var(--diff-added-text)}
.diff .del{background:var(--diff-removed-background);color:var(--diff-removed-text)}
details pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice{border-left:4px solid var(--warning);
padding:10px 12px;background:var(--code-background)}.danger{border-left-color:var(--failure)}
a{color:var(--link)}a:visited{color:var(--link-visited)}
a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid var(--focus-ring);outline-offset:3px}
dl.grid{display:grid;grid-template-columns:minmax(170px,260px) 1fr;
gap:7px 16px}dt{font-weight:600}dd{margin:0;overflow-wrap:anywhere}
@media(max-width:650px){main{padding:14px}dl.grid{grid-template-columns:1fr}table{font-size:12px}}
@media print{:root,:root[data-theme="dark"]{color-scheme:light;--page-background:#fff;
--panel-background:#fff;--text-primary:#000;--text-muted:#333;--border:#777;--link:#000;
--link-visited:#000;--success:#174b32;--warning:#604000;--failure:#781c16;
--code-background:#f1f1f1;--diff-added-background:#e4f3e8;--diff-added-text:#123d27;
--diff-removed-background:#f8e5e2;--diff-removed-text:#641813;--focus-ring:#000}
.report-tools{display:none}body{font-size:11pt}main{max-width:none;padding:0}section,.card{box-shadow:none;break-inside:avoid}}
""".strip()


THEME_JS = """(function () {
  'use strict';
  var STORAGE_KEY = 'evidence-archive-report-theme';
  var VALID_THEMES = ['system', 'light', 'dark'];

  function validTheme(value) {
    return VALID_THEMES.indexOf(value) !== -1 ? value : 'system';
  }

  function readTheme() {
    try { return validTheme(window.localStorage.getItem(STORAGE_KEY)); }
    catch (error) { return 'system'; }
  }

  function applyTheme(value) {
    var theme = validTheme(value);
    if (theme === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', theme);
    document.querySelectorAll('[data-theme-selector]').forEach(function (selector) {
      selector.value = theme;
    });
    return theme;
  }

  function saveTheme(value) {
    try { window.localStorage.setItem(STORAGE_KEY, value); }
    catch (error) { /* Theme switching remains available for this page. */ }
  }

  var initialTheme = readTheme();
  applyTheme(initialTheme);

  document.addEventListener('DOMContentLoaded', function () {
    applyTheme(initialTheme);
    document.querySelectorAll('[data-theme-selector]').forEach(function (selector) {
      selector.addEventListener('change', function (event) {
        var theme = applyTheme(event.target.value);
        saveTheme(theme);
      });
    });
  });
}());
"""


def _h(value):
    return escape("" if value is None else str(value), quote=True)


def _utc(value):
    if not value:
        return "unknown"
    return str(value).replace("+00:00", "Z") + (" UTC" if "Z" not in str(value) else "")


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _page(title, body, depth=0):
    prefix = "../" * depth
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'">
<title>{_h(title)}</title><script src="{prefix}assets/theme.js"></script>
<link rel="stylesheet" href="{prefix}assets/report.css"></head>
<body><main><div class="report-tools"><div class="theme-control">
<label for="theme-select">Theme</label><select id="theme-select" data-theme-selector>
<option value="system">System default</option><option value="light">Light</option>
<option value="dark">Dark</option></select></div></div>{body}</main></body></html>"""


def _query(store, sql, params=()):
    with store._connect() as conn:  # noqa: SLF001 - read-only report query
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _configs(config_path):
    """Load check configuration when available, without requiring it."""
    if not config_path:
        candidates = [Path.cwd() / "kibitzr.yml"]
        config_path = next((p for p in candidates if p.exists()), None)
    if not config_path:
        return {}
    from kibitzr.conf import ReloadableSettings  # noqa: PLC0415

    settings = ReloadableSettings(str(Path(config_path).expanduser().resolve().parent))
    return {item["name"]: item for item in settings.checks}


def _plugin_version():
    try:
        from importlib.metadata import version  # noqa: PLC0415
        return version("kibitzr-archive")
    except Exception:  # noqa: BLE001
        return "unknown"


def _git_commit():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], capture_output=True,
            text=True, timeout=2, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _verification_result(store):
    findings, _counts = integrity.check(store)
    chains_ok = all(
        store.verify_chain(name)[0]
        and store.verify_normalisation_chain(name)[0]
        for name in store.check_names()
    ) and store.verify_annotation_chain()[0]
    return "verified" if not integrity.broken(findings) and chains_ok else "broken"


class _ReportHTMLParser(html.parser.HTMLParser):
    """Collect local references and fragment targets without executing HTML."""

    def __init__(self):
        super().__init__()
        self.references = []
        self.identifiers = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.identifiers.add(values["id"])
        for attribute in ("href", "src"):
            if values.get(attribute):
                self.references.append((tag, attribute, values[attribute]))


def validate_report(root):
    """Reject broken local references and machine-specific path disclosure."""
    root = Path(root).resolve()
    pages = sorted(root.rglob("*.html"))
    if not pages:
        raise ValueError("generated report contains no HTML pages")
    parsed = {}
    forbidden = ("/home/", "file://")
    windows_path = re.compile(r"[A-Za-z]:\\Users\\")
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if any(value in text for value in forbidden) or windows_path.search(text):
            raise ValueError(f"generated page discloses a development-machine path: {page.name}")
        parser = _ReportHTMLParser()
        parser.feed(text)
        parsed[page] = parser

    failures = []
    for page, parser in parsed.items():
        for _tag, _attribute, reference in parser.references:
            split = urlsplit(reference)
            if split.scheme or split.netloc:
                if _attribute == "src" or _tag == "link":
                    failures.append(
                        f"{page.relative_to(root)} has remote asset: {reference}")
                continue
            if not split.path:
                target = page
            else:
                target = (page.parent / unquote(split.path)).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    failures.append(f"{page.relative_to(root)} escapes report root: {reference}")
                    continue
            if not target.exists():
                failures.append(f"{page.relative_to(root)} has missing target: {reference}")
                continue
            if split.fragment and target.suffix.lower() == ".html":
                target_parser = parsed.get(target)
                if target_parser is None:
                    target_parser = _ReportHTMLParser()
                    target_parser.feed(target.read_text(encoding="utf-8"))
                    parsed[target] = target_parser
                if unquote(split.fragment) not in target_parser.identifiers:
                    failures.append(f"{page.relative_to(root)} has missing fragment: {reference}")
    if failures:
        raise ValueError("invalid generated report links:\n" + "\n".join(failures))
    return {"html_pages": len(pages), "references_checked": sum(
        len(parser.references) for parser in parsed.values())}


def _publication_manifest(store, target, generated, verification_result):
    """Describe this derived publication build, never the evidential archive."""
    return {
        "schema_version": 1,
        "report_kind": "derived-evidence-archive-report",
        "generated_at": generated,
        "generator_version": _plugin_version(),
        "generator_commit": _git_commit(),
        "verification_result": verification_result,
        "verification_time": generated,
        "poll_count": store.stats()["polls"],
        "target_count": len(store.check_names()),
        "published_change_page_count": len(list((target / "changes").glob("poll-*.html"))),
        "published_response_count": len(list((target / "responses").glob("*.txt"))),
    }


def _normalise(raw, conf, expected_transform):
    """Re-run only the document-producing portion of a Kibitzr pipeline."""
    rules = transform_rules(conf)
    if transform_id(rules) != expected_transform:
        raise ValueError("current extraction rules do not match the recorded transform fingerprint")
    stop = capture_index(rules)
    document_rules = rules[:stop]
    try:
        text = raw.decode(conf.get("encoding") or "utf-8")
    except (LookupError, UnicodeDecodeError) as exc:
        raise ValueError(f"raw response cannot be decoded with the configured encoding: {exc}") from exc
    from kibitzr.transformer.factory import TransformPipeline  # noqa: PLC0415

    pipeline = TransformPipeline(dict(conf, transform=document_rules))
    if stop < len(rules):
        ok, value = True, text
        for transform in pipeline.transforms:
            if not ok:
                break
            ok, value = transform(value)
    else:
        ok, value = pipeline(True, text)
    if not ok or value is None:
        raise ValueError("the historical response no longer passes the configured extraction pipeline")
    encoded = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return value if isinstance(value, str) else encoded.decode("utf-8"), encoded


def _reconstruct(store, before_poll, before_norm, after_poll, after_norm, conf):
    if not conf:
        return None, None, "current transform rules are unavailable"
    if before_norm["transform_id"] != after_norm["transform_id"]:
        return None, None, ("Extraction rules changed between these observations. "
                            "This difference cannot be attributed solely to the publisher.")
    try:
        before_raw = store.get_blob(before_poll["content_sha256"])
        after_raw = store.get_blob(after_poll["content_sha256"])
    except FileNotFoundError as exc:
        return None, None, f"a retained raw response is missing: {exc}"
    except (BlobMismatch, OSError, EOFError) as exc:
        return None, None, f"a retained raw response failed hash verification: {exc}"
    try:
        before, before_bytes = _normalise(before_raw, conf, before_norm["transform_id"])
        after, after_bytes = _normalise(after_raw, conf, after_norm["transform_id"])
    except (ValueError, RuntimeError, OSError) as exc:
        return None, None, str(exc)
    for label, material, row in (("before", before_bytes, before_norm),
                                 ("after", after_bytes, after_norm)):
        actual = sha256_hex(material)
        if actual != row["content_sha256"]:
            return None, None, (f"re-derived {label} document hashes to {actual}, not the "
                                f"recorded {row['content_sha256']}")
    return before, after, None


def _inline_diff(before, after):
    pieces = []
    for item in difflib.ndiff(before.splitlines(keepends=True), after.splitlines(keepends=True)):
        if item.startswith("? "):
            continue
        css = "add" if item.startswith("+ ") else "del" if item.startswith("- ") else ""
        prefix = item[:2]
        pieces.append(f'<span class="{css}">{_h(prefix + item[2:])}</span>')
    return "".join(pieces)


def _json_changes(before, after):
    try:
        left, right = json.loads(before), json.loads(after)
    except (TypeError, ValueError):
        return []
    changes = []

    def walk(a, b, path="$", depth=0):
        if depth > 12:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                child = f"{path}.{key}"
                if key not in a:
                    changes.append((child, "added", None, b[key]))
                elif key not in b:
                    changes.append((child, "removed", a[key], None))
                else:
                    walk(a[key], b[key], child, depth + 1)
        elif isinstance(a, list) and isinstance(b, list):
            if a != b:
                changes.append((path, "changed", a, b))
        elif a != b:
            changes.append((path, "changed", a, b))

    walk(left, right)
    return changes[:250]


def _ocds_items(document):
    try:
        data = json.loads(document)
    except (TypeError, ValueError):
        return None
    releases = data.get("releases") if isinstance(data, dict) else data
    if not isinstance(releases, list) or not all(isinstance(x, dict) for x in releases):
        return None
    return {str(item.get("ocid")): item for item in releases if item.get("ocid")}


def _dig(item, *paths):
    for path in paths:
        value = item
        try:
            for part in path:
                value = value[part]
        except (KeyError, TypeError, IndexError):
            continue
        if value is not None:
            return value
    return None


def _supplier_names(item):
    awards = item.get("awards") or []
    return sorted({str(s.get("name")) for award in awards for s in (award.get("suppliers") or []) if s.get("name")})


def _ocds_summary(before, after):
    left, right = _ocds_items(before), _ocds_items(after)
    if left is None or right is None:
        return []
    out = []
    for ocid in sorted(left.keys() | right.keys()):
        if ocid not in left:
            out.append(f"Release added: {ocid}")
            continue
        if ocid not in right:
            out.append(f"Release removed: {ocid}")
            continue
        a, b = left[ocid], right[ocid]
        fields = [
            ("Title", _dig(a, ("tender", "title"), ("title",)), _dig(b, ("tender", "title"), ("title",))),
            ("Buyer", _dig(a, ("buyer", "name")), _dig(b, ("buyer", "name"))),
            ("Supplier", _supplier_names(a), _supplier_names(b)),
            ("Contract value", _dig(a, ("awards", 0, "value", "amount"), ("contracts", 0, "value", "amount")), _dig(b, ("awards", 0, "value", "amount"), ("contracts", 0, "value", "amount"))),
            ("Currency", _dig(a, ("awards", 0, "value", "currency"), ("contracts", 0, "value", "currency")), _dig(b, ("awards", 0, "value", "currency"), ("contracts", 0, "value", "currency"))),
            ("Award date", _dig(a, ("awards", 0, "date"), ("date",)), _dig(b, ("awards", 0, "date"), ("date",))),
            ("Status", _dig(a, ("awards", 0, "status"), ("tender", "status"), ("status",)), _dig(b, ("awards", 0, "status"), ("tender", "status"), ("status",))),
        ]
        for label, old, new in fields:
            if old != new:
                out.append(f"{ocid} — {label} changed: {old!s} → {new!s}")
    return out


def _annotations(store, check_name, before_poll, after_poll):
    selected = []
    lo, hi = before_poll["id"], after_poll["id"]
    start, end = before_poll["polled_at"], after_poll["polled_at"]
    for item in store.annotations(check_name=check_name):
        subjects_overlap = (item["subject_from"] is not None and
                            item["subject_from"] <= hi and
                            (item["subject_to"] is None or item["subject_to"] >= lo))
        effective = item["effective_from"] <= end
        names_check = item["check_name"] in (None, check_name)
        if names_check and (subjects_overlap or effective or start <= item["recorded_at"] <= end):
            selected.append(item)
    return selected


def _anchor_coverage(store, poll):
    """Find the earliest structurally valid manifest that covers a poll."""
    anchor_rows = store.anchors()
    statuses = {row["manifest_ref"]: row["status"] for row in anchor_rows}
    root = Path(store.root) / ANCHOR_DIR
    candidates = []
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            try:
                raw = path.read_bytes()
                manifest = json.loads(raw)
                proof = path.with_suffix(path.suffix + ".ots").read_bytes()
                _algorithm, covered = committed_digest(proof)
                if covered != sha256_hex(raw):
                    continue
                entry = next((e for e in manifest["checks"] if e.get("check") == poll["check_name"]), None)
                if not entry or (entry.get("last_poll_id") or 0) < poll["id"]:
                    continue
                findings = []
                integrity._check_manifest_entry(  # noqa: SLF001 - same authoritative verifier path
                    integrity._Chains(store), path.name, entry, findings)  # noqa: SLF001
                if any(f.severity == integrity.BROKEN for f in findings):
                    continue
                ref = os.path.join(ANCHOR_DIR, path.name)
                candidates.append((manifest.get("created_at", ""), ref, manifest, statuses.get(ref, "pending")))
            except (OSError, ValueError, KeyError, TypeError, ProofFormatError):
                continue
    if not candidates:
        return {"state": "unanchored", "label": "Unanchored"}
    created, ref, manifest, status = sorted(candidates)[0]
    state = "complete" if status == "complete" else "pending"
    label = "Bitcoin-attested" if state == "complete" else "Pending calendar attestation"
    return {"state": state, "label": label, "created_at": created,
            "manifest_ref": ref, "proof_ref": ref + ".ots", "manifest": manifest}


def _copy_evidence(store, target, digest):
    raw = store.get_blob(digest)
    (target / "responses" / f"{digest}.txt").write_bytes(raw)
    return True


def _copy_anchor(store, target, coverage):
    if coverage["state"] == "unanchored":
        return
    for key in ("manifest_ref", "proof_ref"):
        source = Path(store.root) / coverage[key]
        suffix = ".json.txt" if key == "manifest_ref" else ".ots"
        destination = target / "anchors" / (source.name.split(".json")[0] + suffix)
        if not destination.exists():
            destination.write_bytes(source.read_bytes())
        coverage[key + "_output"] = f"../anchors/{destination.name}"


def _evidence_grid(poll, norm, prefix):
    return "".join([
        f"<dt>{_h(prefix)} poll ID</dt><dd>{poll['id']}</dd>",
        f"<dt>{_h(prefix)} raw SHA-256</dt><dd class=hash>{_h(poll['content_sha256'])}</dd>",
        f"<dt>{_h(prefix)} normalisation SHA-256</dt><dd class=hash>{_h(norm['content_sha256'])}</dd>",
        f"<dt>{_h(prefix)} poll record hash</dt><dd class=hash>{_h(poll['record_hash'])}</dd>",
        f"<dt>{_h(prefix)} normalisation record hash</dt><dd class=hash>{_h(norm['record_hash'])}</dd>",
    ])


def _change_page(store, target, before_poll, before_norm, after_poll, after_norm,
                 conf, chains_ok):
    before, after, unavailable = _reconstruct(
        store, before_poll, before_norm, after_poll, after_norm, conf)
    coverage = _anchor_coverage(store, after_poll)
    copied = {}
    for poll in (before_poll, after_poll):
        try:
            copied[poll["id"]] = _copy_evidence(
                store, target, poll["content_sha256"])
        except (FileNotFoundError, BlobMismatch, OSError, EOFError):
            copied[poll["id"]] = False
    _copy_anchor(store, target, coverage)
    schedule = None
    annotations = _annotations(store, after_poll["check_name"], before_poll, after_poll)
    for item in annotations:
        if item["kind"] == "schedule" and item["effective_from"] <= after_poll["polled_at"]:
            schedule = item["detail"].get("period")
    try:
        a = datetime.fromisoformat(before_poll["polled_at"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(after_poll["polled_at"].replace("Z", "+00:00"))
        gap = str(b - a)
    except (TypeError, ValueError):
        gap = "unknown"

    if unavailable:
        summary = f'<p class="notice danger"><strong>Verified document diff unavailable.</strong> {_h(unavailable)}</p>'
    else:
        domain = _ocds_summary(before, after)
        structured = _json_changes(before, after)
        facts = domain or [f"{path}: {_h(old)} → {_h(new)}" for path, _kind, old, new in structured]
        fact_html = "".join(
            f"<li>{escape(item, quote=False) if domain else item}</li>"
            for item in facts)
        summary = ((f"<h3>Changed fields</h3><ul>{fact_html}</ul>" if facts else "") +
                   f'<h3>Readable diff</h3><div class="diff">{_inline_diff(before, after)}</div>' +
                   f'<details><summary>Complete before document</summary><pre>{_h(before)}</pre></details>' +
                   f'<details><summary>Complete after document</summary><pre>{_h(after)}</pre></details>')

    ann_html = "".join(
        f"<li><strong>{_h(item['kind'])}</strong> — effective {_h(_utc(item['effective_from']))}; "
        f"recorded {_h(_utc(item['recorded_at']))}<pre>{_h(json.dumps(item['detail'], ensure_ascii=False, indent=2))}</pre></li>"
        for item in annotations) or "<li>No applicable annotations recorded.</li>"
    anchor_extra = ""
    if coverage["state"] != "unanchored":
        anchor_extra = (f"<p>Manifest time: {_h(_utc(coverage['created_at']))}. "
                        f"<a download href=\"{_h(coverage['manifest_ref_output'])}\">Inert manifest copy</a> · "
                        f"<a download href=\"{_h(coverage['proof_ref_output'])}\">OpenTimestamps proof</a></p>")
    else:
        anchor_extra = '<p class="notice danger">This poll is not covered by a completed or pending proof.</p>'
    schedule_text = f"every {schedule:g} seconds" if isinstance(schedule, (int, float)) else "not recorded"
    before_raw_link = (
        f'<a download href="../responses/{before_poll["content_sha256"]}.txt">Before raw response</a>'
        if copied[before_poll["id"]] else "Before raw response unavailable")
    after_raw_link = (
        f'<a download href="../responses/{after_poll["content_sha256"]}.txt">After raw response</a>'
        if copied[after_poll["id"]] else "After raw response unavailable")
    body = f"""
<p><a href="../index.html">← Evidence archive dashboard</a></p>
<h1>{_h(after_poll['check_name'])}</h1><p class="sub">Document change observed at {_h(_utc(after_poll['polled_at']))}</p>
<section><h2>Observation bracket</h2><dl class="grid">
<dt>Previous observation</dt><dd>Poll {before_poll['id']} at {_h(_utc(before_poll['polled_at']))}</dd>
<dt>New observation</dt><dd>Poll {after_poll['id']} at {_h(_utc(after_poll['polled_at']))}</dd>
<dt>Configured schedule</dt><dd>{_h(schedule_text)}</dd><dt>Observed gap</dt><dd>{_h(gap)}</dd></dl>
<p>The change occurred sometime inside this bracket, not necessarily at the later timestamp.</p></section>
<section><h2>Change summary</h2>{summary}</section>
<section><h2>Evidence</h2><dl class="grid"><dt>Check</dt><dd>{_h(after_poll['check_name'])}</dd>
<dt>Configured URL</dt><dd>{_h((conf or {}).get('url') or after_poll.get('url') or 'not recorded')}</dd>
{_evidence_grid(before_poll, before_norm, 'Before')}{_evidence_grid(after_poll, after_norm, 'After')}
<dt>Transform fingerprint</dt><dd class=hash>{_h(after_norm['transform_id'])}</dd>
<dt>Fetch fingerprint</dt><dd class=hash>{_h(after_poll.get('fetch_id') or 'not recorded')}</dd>
<dt>Poll chain during generation</dt><dd>{'Verified' if chains_ok[0] else 'Broken'}</dd>
<dt>Document chain during generation</dt><dd>{'Verified' if chains_ok[1] else 'Broken'}</dd></dl></section>
<section><h2>Timestamp coverage</h2><p><span class="pill {_h(coverage['state'])}">{_h(coverage['label'])}</span></p>{anchor_extra}
<pre>kibitzr archive verify --root archive
kibitzr archive fsck --root archive
python deploy/verify_independently.py archive
ots verify anchors/&lt;manifest&gt;.json.ots</pre></section>
<section><h2>Annotations</h2><ul>{ann_html}</ul></section>
<section><h2>Raw responses</h2><p>These downloads use <code>.txt</code> and are not embedded or executed.</p>
<p>{before_raw_link} · {after_raw_link}</p></section>"""
    filename = f"poll-{after_poll['id']:06d}.html"
    (target / "changes" / filename).write_text(_page(
        f"{after_poll['check_name']} change", body, depth=1), encoding="utf-8")
    return filename, unavailable


def _recent_rows(store, target, configs, chain_state):
    rows = _query(store, """
        SELECT p.*, n.id AS norm_id, n.recorded_at AS norm_recorded_at,
               n.content_sha256 AS norm_content_sha256, n.content_length AS norm_content_length,
               n.transform_id AS norm_transform_id, n.changed AS norm_changed,
               n.prev_hash AS norm_prev_hash, n.record_hash AS norm_record_hash
          FROM poll p LEFT JOIN normalisation n ON n.poll_id = p.id
         ORDER BY p.id DESC LIMIT 100
    """)
    output = []
    for row in rows:
        norm = None if row["norm_id"] is None else {
            "id": row["norm_id"], "recorded_at": row["norm_recorded_at"],
            "content_sha256": row["norm_content_sha256"], "content_length": row["norm_content_length"],
            "transform_id": row["norm_transform_id"], "changed": row["norm_changed"],
            "prev_hash": row["norm_prev_hash"], "record_hash": row["norm_record_hash"],
            "poll_id": row["id"], "check_name": row["check_name"],
        }
        result = "Success" if row["ok"] else "Failed"
        raw = "Yes" if row["ok"] and row["changed"] else "No" if row["ok"] else "—"
        detail = ""
        if not row["ok"]:
            document = "—"
        elif norm is None:
            document = "Not recorded"
        else:
            previous = _query(store, "SELECT * FROM normalisation WHERE check_name=? AND id<? ORDER BY id DESC LIMIT 1", (row["check_name"], norm["id"]))
            if not previous:
                document = "Initial capture"
            elif norm["changed"]:
                document = "Yes"
                before_norm = previous[0]
                before_poll_rows = _query(store, "SELECT * FROM poll WHERE id=?", (before_norm["poll_id"],))
                if before_poll_rows:
                    flags = chain_state.get(row["check_name"], (False, False))
                    filename, unavailable = _change_page(
                        store, target, before_poll_rows[0], before_norm, row, norm,
                        configs.get(row["check_name"]), flags)
                    label = "View change" if unavailable is None else "View evidence"
                    detail = f'<a href="changes/{filename}">{label}</a>'
            else:
                document = "No"
                if row["changed"] and row["content_sha256"]:
                    try:
                        _copy_evidence(store, target, row["content_sha256"])
                        detail = f'<a class="muted" download href="responses/{row["content_sha256"]}.txt">View raw response</a>'
                    except (FileNotFoundError, BlobMismatch, OSError, EOFError):
                        detail = "Raw response unavailable"
        output.append("<tr>" + "".join([
            f"<td>{row['id']}</td><td>{_h(_utc(row['polled_at']))}</td><td>{_h(row['check_name'])}</td>",
            f"<td>{_h(result)}</td><td>{_h(raw)}</td><td>{_h(document)}</td><td>{detail}</td>",
            f"<td title=\"{_h(row.get('error'))}\">{_h((row.get('error') or '')[:100])}</td>",
        ]) + "</tr>")
    return output


def _render_index(store, target, configs, archive_label, generated):
    findings, counts = integrity.check(store)
    broken = integrity.broken(findings)
    names = store.check_names()
    chain_state = {name: (store.verify_chain(name)[0], store.verify_normalisation_chain(name)[0]) for name in names}
    annotations_ok = store.verify_annotation_chain()[0]
    integrity_state = "Verified" if not broken and all(all(v) for v in chain_state.values()) and annotations_ok else "Broken"
    latest = [store.last_poll(name) for name in names]
    current_failures = [row for row in latest if row is not None and not row["ok"]]
    collection_state = "Current failures" if current_failures else "Latest cycle succeeded"
    anchors = store.anchors()
    complete = len({r["manifest_ref"] for r in anchors if r["status"] == "complete"})
    pending = len({r["manifest_ref"] for r in anchors if r["status"] == "pending"})
    timestamp_state = f"{complete} completed, {pending} pending, {counts['exposed']} unanchored polls"
    recent = _recent_rows(store, target, configs, chain_state)
    checks = []
    controls = store.control_checks()
    for name in names:
        stats, last = store.stats(name), store.last_poll(name)
        checks.append("<tr>" + "".join([
            f"<td>{_h(name)}{' <span class=pill>control</span>' if name in controls else ''}</td>",
            f"<td>{stats['polls']}</td><td>{stats['failures']}</td><td>{stats['changes']}</td>",
            f"<td>{stats['normalised_changes']}</td><td>{store.unanchored_polls(name)}</td>",
            f"<td>{_h(_utc(last['polled_at'] if last else None))}</td>",
        ]) + "</tr>")
    finding_rows = "".join(
        f'<li class="{_h(f.severity)}"><strong>{_h(f.severity)}</strong> {_h(f.kind)} — {_h(f.detail)}</li>'
        for f in findings) or '<li class="good">No integrity findings.</li>'
    total = store.stats()
    body = f"""
<h1>Evidence archive dashboard</h1><p class="sub">Archive: {_h(archive_label)} · Generated {_h(_utc(generated))}.</p>
<div class="cards"><div class="card {'good' if integrity_state == 'Verified' else 'bad'}"><span>Archive integrity</span><strong>{integrity_state}</strong></div>
<div class="card {'bad' if current_failures else 'good'}"><span>Collection health</span><strong>{_h(collection_state)}</strong></div>
<div class="card {'warn' if counts['exposed'] or pending else 'good'}"><span>Timestamp coverage</span><strong>{_h(timestamp_state)}</strong></div>
<div class="card"><span>Polls / lifetime failures</span><strong>{total['polls']} / {total['failures']}</strong></div></div>
<section><h2>About this view</h2><p>This static report is derived from the archive; it is not evidence itself. Raw change means fetched response bytes moved. Document change means the content selected by the recorded transform moved. Only hash-reproduced documents receive a verified diff.</p>
<p><a href="publication-manifest.json">Publication build manifest</a> — provenance for this derived export, not an evidential archive manifest.</p>
<dl class="grid"><dt>Verification time</dt><dd>{_h(_utc(generated))}</dd><dt>Plugin version</dt><dd>{_h(_plugin_version())}</dd><dt>Git commit</dt><dd>{_h(_git_commit())}</dd><dt>Times</dt><dd>All report times are UTC.</dd></dl></section>
<section><h2>Checks</h2><table><thead><tr><th>Check</th><th>Polls</th><th>Lifetime failures</th><th>Raw changes</th><th>Document changes</th><th>Unanchored</th><th>Latest poll (UTC)</th></tr></thead><tbody>{''.join(checks)}</tbody></table></section>
<section><h2>Integrity findings</h2><ul>{finding_rows}</ul></section>
<section><h2>Recent polls</h2><table><thead><tr><th>ID</th><th>Time (UTC)</th><th>Check</th><th>Result</th><th>Raw changed</th><th>Document changed</th><th>Detail</th><th>Error</th></tr></thead><tbody>{''.join(recent)}</tbody></table></section>
<section><h2>Third-party verification</h2><pre>kibitzr archive verify --root archive
kibitzr archive fsck --root archive
python deploy/verify_independently.py archive</pre></section>"""
    return _page("Evidence archive dashboard", body)


def write(store, output, config_path=None, archive_label="Evidence archive"):
    """Generate a complete static report directory and replace atomically."""
    output = Path(output).expanduser().resolve()
    if output == Path(store.root).resolve() or Path(store.root).resolve() in output.parents:
        raise ValueError("report output must be outside the archive root")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    backup = output.with_name(f".{output.name}.previous")
    try:
        for dirname in ("assets", "changes", "responses", "anchors"):
            (temporary / dirname).mkdir()
        (temporary / "assets" / "report.css").write_text(CSS, encoding="utf-8")
        (temporary / "assets" / "theme.js").write_text(THEME_JS, encoding="utf-8")
        configs = _configs(config_path)
        generated = _now()
        index = _render_index(store, temporary, configs, archive_label, generated)
        (temporary / "index.html").write_text(index, encoding="utf-8")
        manifest = _publication_manifest(
            store, temporary, generated, _verification_result(store))
        (temporary / "publication-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n", encoding="utf-8")
        validate_report(temporary)
        if backup.exists():
            shutil.rmtree(backup)
        if output.exists():
            os.replace(output, backup)
        try:
            os.replace(temporary, output)
        except Exception:
            if backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return output
