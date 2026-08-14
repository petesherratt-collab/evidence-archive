"""Deterministic, read-only export for the public evidence browser.

The exporter deliberately does not use :class:`ArchiveStore` to open the
source archive.  ``ArchiveStore`` is the collector's write-capable interface;
the public boundary opens SQLite in ``mode=ro`` and reads retained blobs by
the digest committed to the poll chain.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import __version__
from .anchor import ProofFormatError, committed_digest
from .store import (
    ANNOTATION_CHAIN_VERSION,
    GENESIS,
    NORMALISATION_CHAIN_VERSION,
    POLL_CHAIN_VERSION,
    compute_record_hash,
    poll_hash_fields,
    sha256_hex,
)


SCHEMA_VERSION = "1.0.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# This is descriptive metadata for the existing watch list, not a second
# source of evidence. URLs on poll rows remain the authoritative observed URL.
SOURCE_DEFINITIONS = {
    "Contracts Finder — recent awards": {
        "slug": "contracts-finder-recent-awards",
        "source_type": "ocds_api",
        "category": "government_contracts",
        "extractor": "ocds_awards",
        "configured_url": "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?stages=award",
    },
    "Find a Tender — recent awards": {
        "slug": "find-a-tender-recent-awards",
        "source_type": "ocds_api",
        "category": "government_contracts",
        "extractor": "ocds_awards",
        "configured_url": "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages",
    },
    "Government Commercial Agency — agreements": {
        "slug": "government-commercial-agency-agreements",
        "source_type": "html_publication",
        "category": "frameworks",
        "extractor": None,
        "configured_url": "https://www.gca.gov.uk/agreements",
    },
    "Departmental spend over £25k — monthly release": {
        "slug": "departmental-spend-over-25k-monthly-release",
        "source_type": "html_publication",
        "category": "government_spending",
        "extractor": None,
        "configured_url": "https://www.gov.uk/government/collections/dhsc-spending-over-25000",
    },
    "UK direct awards — no competition": {
        "slug": "uk-direct-awards-no-competition",
        "source_type": "ocds_api",
        "category": "government_contracts",
        "extractor": "ocds_direct_awards",
        "configured_url": "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?stages=award",
    },
}


def _canonical(value):
    """Return canonical JSON bytes used for every public JSON file."""
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("ascii")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _slug(value):
    value = unicodedata.normalize("NFKD", value).encode(
        "ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-") or "source"


def _record_file_id(record_id):
    return hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:24]


def _iso(value):
    if not value:
        return None
    return str(value)


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _max_time(values):
    parsed = [(item, _parse_time(item)) for item in values if item]
    parsed = [(text, value) for text, value in parsed if value is not None]
    return max(parsed, key=lambda pair: pair[1])[0] if parsed else None


def _first_time(values):
    parsed = [(item, _parse_time(item)) for item in values if item]
    parsed = [(text, value) for text, value in parsed if value is not None]
    return min(parsed, key=lambda pair: pair[1])[0] if parsed else None


def _unique(values):
    out = []
    for value in values:
        if value is not None and value not in out:
            out.append(value)
    return out


def _consistent(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    first = values[0]
    return first if all(value == first for value in values[1:]) else None


class ReadOnlyArchive:
    """Read-only view of an archive, with chain and blob verification."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.db_path = self.root / "polls.db"
        if not self.db_path.is_file():
            raise ValueError(f"no archive database at {self.db_path}")
        uri = "file:" + quote(str(self.db_path), safe="/") + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        self._closed = False

    def close(self):
        if not self._closed:
            self.conn.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def rows(self, table, where="", params=()):
        sql = f"SELECT * FROM {table}"
        if where:
            sql += " WHERE " + where
        sql += " ORDER BY id"
        return [dict(row) for row in self.conn.execute(sql, params)]

    def blob(self, digest):
        if not digest or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"invalid retained content digest {digest!r}")
        path = self.root / "blobs" / digest[:2] / (digest + ".gz")
        with gzip.open(path, "rb") as handle:
            data = handle.read()
        actual = sha256_hex(data)
        if actual != digest:
            raise ValueError(
                f"retained blob {digest} hashes to {actual}; refusing export")
        return data

    def check_names(self):
        return [row[0] for row in self.conn.execute(
            "SELECT DISTINCT check_name FROM poll ORDER BY check_name")]


def _poll_fields(row):
    return poll_hash_fields(
        row["check_name"], row["url"], row["polled_at"], row["ok"],
        row["http_status"], row["content_sha256"], row["changed"],
        row.get("fetch_id"))


def _norm_fields(row):
    return {
        "check": row["check_name"],
        "poll_id": row["poll_id"],
        "recorded_at": row["recorded_at"],
        "content_sha256": row["content_sha256"],
        "transform_id": row["transform_id"],
        "changed": bool(row["changed"]),
    }


def _annotation_fields(row):
    return {
        "kind": row["kind"],
        "check": row["check_name"],
        "effective_from": row["effective_from"],
        "recorded_at": row["recorded_at"],
        "subject_from": row["subject_from"],
        "subject_to": row["subject_to"],
        "detail": row["detail"],
    }


def _verify_chain(rows, fields, version):
    previous = GENESIS
    hashes = {}
    for row in rows:
        if row["prev_hash"] != previous:
            raise ValueError(f"chain link mismatch at row {row['id']}")
        expected = compute_record_hash(fields(row), previous, version=version)
        if expected != row["record_hash"]:
            raise ValueError(f"record hash mismatch at row {row['id']}")
        hashes[row["id"]] = expected
        previous = expected
    return hashes, previous


def _anchor_state(archive, poll, poll_hashes, norm_hashes,
                  annotation_hashes):
    """Return the strongest valid coverage for one poll row."""
    candidates = []
    invalid_coverage = False
    for row in archive.rows("anchor", "check_name = ?", (poll["check_name"],)):
        if row["status"] == "failed" or not row.get("proof_ref"):
            continue
        manifest_path = archive.root / row["manifest_ref"]
        proof_path = archive.root / row["proof_ref"]
        if not manifest_path.is_file() or not proof_path.is_file():
            invalid_coverage = True
            continue
        try:
            manifest_bytes = manifest_path.read_bytes()
            if sha256_hex(manifest_bytes) != row["manifest_sha256"]:
                invalid_coverage = True
                continue
            _algorithm, covered = committed_digest(proof_path.read_bytes())
            if covered != row["manifest_sha256"]:
                invalid_coverage = True
                continue
            manifest = json.loads(manifest_bytes)
            entry = next((item for item in manifest.get("checks", [])
                          if item.get("check") == poll["check_name"]), None)
            if not entry or (entry.get("last_poll_id") or 0) < poll["id"]:
                continue
            last_id = entry["last_poll_id"]
            if poll_hashes.get(last_id) != entry.get("poll_head"):
                continue
            if entry.get("norm_head") not in set(norm_hashes.values()) | {GENESIS}:
                continue
            if entry.get("annotation_head") not in set(annotation_hashes.values()) | {GENESIS}:
                continue
            status = "bitcoin-backed" if row["status"] == "complete" else "pending"
            candidates.append((last_id, status, row, manifest))
        except (OSError, ValueError, KeyError, TypeError, ProofFormatError):
            invalid_coverage = True
            continue
    if not candidates:
        if invalid_coverage:
            return {"status": "verification-failed", "coverage": "verification-failed"}
        return {"status": "none", "coverage": "awaiting-coverage"}
    rank = {"none": 0, "pending": 1, "bitcoin-backed": 2}
    _last_id, status, row, manifest = max(
        candidates, key=lambda item: (item[0], rank[item[1]]))
    return {
        "status": status,
        "coverage": "covered",
        "manifest_sha256": row["manifest_sha256"],
        "submitted_at": row["anchored_at"],
        "manifest_ref": row["manifest_ref"],
        "proof_ref": row["proof_ref"],
        "head_sha256": next(
            item["combined_head"] for item in manifest["checks"]
            if item["check"] == poll["check_name"]),
    }


def _entity(role, raw):
    if not isinstance(raw, dict):
        return None
    name = raw.get("name") or raw.get("identifier", {}).get("legalName")
    identifier = raw.get("id")
    if not name and not identifier:
        return None
    value = identifier or name
    entity_id = f"entity:{role}:{value}"
    return {"id": entity_id, "name": name, "role": role,
            "source_identifier": identifier}


def _party_for_role(parties, role, preferred=None):
    preferred = preferred if isinstance(preferred, dict) else {}
    direct = _entity(role, preferred)
    if direct:
        return direct
    for party in parties or []:
        if role in (party.get("roles") or []):
            entity = _entity(role, party)
            if entity:
                return entity
    return None


def _cpv_values(tender):
    values = []
    for item in [tender.get("classification") or {}] + [
        classification
        for item in (tender.get("items") or [])
        for classification in (item.get("additionalClassifications") or [])
    ]:
        if item.get("scheme", "").upper() == "CPV" and item.get("id"):
            values.append({"code": str(item["id"]),
                           "description": item.get("description")})
    return sorted({json.dumps(item, sort_keys=True): item for item in values}.values(),
                  key=lambda item: item["code"])


def _record_snapshot(release, source_id):
    tender = release.get("tender") or {}
    parties = release.get("parties") or []
    awards = [item for item in (release.get("awards") or []) if isinstance(item, dict)]
    if not release.get("ocid") and not release.get("id"):
        return None
    source_key = release.get("ocid") or release.get("id")
    record_id = f"record:{source_id}:{source_key}"
    buyers = _party_for_role(parties, "buyer", release.get("buyer"))
    suppliers = []
    for award in awards:
        for supplier in award.get("suppliers") or []:
            entity = _entity("supplier", supplier)
            if entity and entity not in suppliers:
                suppliers.append(entity)
    if not suppliers:
        for party in parties:
            if "supplier" in (party.get("roles") or []):
                entity = _entity("supplier", party)
                if entity and entity not in suppliers:
                    suppliers.append(entity)

    award_values = [award.get("value") for award in awards]
    value = _consistent(award_values) or (tender.get("value") if not awards else None)
    award_dates = [award.get("date") for award in awards]
    award_date = _consistent(award_dates)
    published = release.get("publishedDate")
    if not published:
        published = _consistent([
            document.get("datePublished")
            for award in awards
            for document in (award.get("documents") or [])
        ])
    if not published:
        published = release.get("date")
    periods = [award.get("contractPeriod") for award in awards]
    if not periods:
        periods = [tender.get("contractPeriod")]
    starts = _consistent([period.get("startDate") for period in periods if period])
    ends = _consistent([period.get("endDate") for period in periods if period])
    classification = _cpv_values(tender)
    status_values = [award.get("status") for award in awards]
    status = _consistent(status_values) or (tender.get("status") if not awards else None)
    notice_urls = [
        document.get("url") for award in awards
        for document in (award.get("documents") or [])
        if document.get("url")
    ] + [document.get("url") for document in (release.get("documents") or [])
         if document.get("url")]
    tags = release.get("tag") or []
    record_type = "award" if awards or "award" in tags or "awardUpdate" in tags else "procurement"
    snapshot = {
        "id": record_id,
        "type": record_type,
        "title": tender.get("title"),
        "buyer": buyers,
        "supplier": suppliers[0] if len(suppliers) == 1 else None,
        "suppliers": suppliers,
        "notice_type": tags[0] if tags else None,
        "dates": {"published": _iso(published), "award": _iso(award_date),
                  "start": _iso(starts), "end": _iso(ends)},
        "value": value if isinstance(value, dict) else None,
        "classification": {"cpv": classification,
                            "category": _consistent([item.get("description")
                                                       for item in classification])},
        "status": status,
        "source": {"notice_url": notice_urls[0] if notice_urls else None},
    }
    return record_id, snapshot


def _extract_structured(body, definition, source_id):
    if not definition.get("extractor"):
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    releases = payload.get("releases") if isinstance(payload, dict) else None
    if not isinstance(releases, list):
        return {}
    out = {}
    for release in releases:
        if not isinstance(release, dict):
            continue
        tags = release.get("tag") or []
        if definition["extractor"] == "ocds_awards":
            if source_id == "find-a-tender-recent-awards" and "award" not in tags:
                continue
        elif definition["extractor"] == "ocds_direct_awards":
            if (release.get("tender") or {}).get("procurementMethod") != "direct":
                continue
        result = _record_snapshot(release, source_id)
        if result:
            out[result[0]] = result[1]
    return out


def _event_id(event):
    body = dict(event)
    body.pop("id", None)
    return "event:" + hashlib.sha256(_canonical(body)).hexdigest()[:24]


def _change_event(target_id, observation_id, previous_observation_id,
                  observed_at, event_type, comparison, record_id=None,
                  title=None):
    event = {
        "target_id": target_id,
        "observation_id": observation_id,
        "previous_observation_id": previous_observation_id,
        "first_observed_at": observed_at,
        "record_id": record_id,
        "title": title,
        "event_type": event_type,
        "comparison": comparison,
    }
    event["id"] = _event_id(event)
    return event


def _compare_record(previous, current):
    fields = (
        ("value", "value_changed"),
        ("dates", "date_changed"),
        ("suppliers", "supplier_changed"),
        ("status", "status_changed"),
    )
    for field, event_type in fields:
        if previous.get(field) != current.get(field):
            yield event_type, {
                "mode": "structured_field",
                "field": field,
                "old": previous.get(field),
                "new": current.get(field),
            }


def _producer_commit():
    repo = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False)
        return result.stdout.strip() or None
    except OSError:
        return None


def _schema_type(value, expected):
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _validate_schema(value, schema, path="$", errors=None):
    errors = errors if errors is not None else []
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_schema_type(value, item) for item in types):
            errors.append(f"{path}: expected {expected}")
            return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _validate_schema(value[key], child, f"{path}.{key}", errors)
    if isinstance(value, list) and schema.get("items"):
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{path}[{index}]", errors)
    if isinstance(value, str) and schema.get("pattern"):
        if not re.fullmatch(schema["pattern"], value):
            errors.append(f"{path}: pattern mismatch")
    if isinstance(value, (int, float)) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
    return errors


def _validate_output(output):
    schema_root = Path(__file__).resolve().parents[3] / \
        "public-browser-prototype" / "schema"
    schema_names = {"manifest.json": "public-export.schema.json"}
    errors = []
    for filename, schema_name in schema_names.items():
        path = output / filename
        if not path.is_file():
            errors.append(f"missing {filename}")
            continue
        errors.extend(_validate_schema(_read_json(path),
                                       _read_json(schema_root / schema_name),
                                       filename))
    aggregate_schemas = {
        "sources.json": ("sources", "source.schema.json"),
        "entities.json": ("entities", "entity.schema.json"),
        "records.json": ("records", "record.schema.json"),
        "changes.json": ("changes", "change.schema.json"),
    }
    for filename, (key, schema_name) in aggregate_schemas.items():
        path = output / filename
        payload = _read_json(path)
        errors.extend(_validate_schema(payload, {
            "type": "object", "required": ["schema_version", key],
            "properties": {"schema_version": {"type": "string"},
                           key: {"type": "array"}},
        }, filename))
        schema = _read_json(schema_root / schema_name)
        for index, item in enumerate(payload.get(key, [])):
            errors.extend(_validate_schema(item, schema,
                                           f"{filename}.{key}[{index}]"))
    observation_schema = _read_json(schema_root / "observation.schema.json")
    record_schema = _read_json(schema_root / "record.schema.json")
    for path in sorted((output / "observations").glob("*.json")):
        payload = _read_json(path)
        errors.extend(_validate_schema(payload, {
            "type": "object",
            "required": ["schema_version", "target_id", "observations"],
            "properties": {
                "schema_version": {"type": "string"},
                "target_id": {"type": "string"},
                "observations": {"type": "array"},
            },
        }, str(path.relative_to(output))))
        for index, observation in enumerate(payload.get("observations", [])):
            errors.extend(_validate_schema(
                observation, observation_schema,
                f"{path.relative_to(output)}.observations[{index}]"))
    for path in sorted((output / "records").glob("*.json")):
        errors.extend(_validate_schema(_read_json(path), record_schema,
                                       str(path.relative_to(output))))
    if errors:
        raise ValueError("public export schema validation failed: " + "; ".join(errors))


def _write_json(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def build_export(archive_root, output, *, include_control=True):
    """Build and atomically publish a public export directory.

    ``generated_at`` is the latest retained observation time, not wall-clock
    time. Consequently two exports from the same archive and producer commit
    are byte-identical.
    """
    output = Path(output).expanduser().resolve()
    archive_root = Path(archive_root).expanduser().resolve()
    if output == archive_root or archive_root in output.parents:
        raise ValueError("public export output must be outside the archive root")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-",
                                      dir=output.parent))
    backup = output.with_name(f".{output.name}.previous")
    try:
        with ReadOnlyArchive(archive_root) as archive:
            polls = archive.rows("poll")
            norms = archive.rows("normalisation")
            annotations = archive.rows("annotation")
            anchors = archive.rows("anchor")
            if not polls:
                raise ValueError("cannot export an empty archive")
            by_check = defaultdict(list)
            for row in polls:
                by_check[row["check_name"]].append(row)
            norm_by_check = defaultdict(list)
            norm_by_poll = {}
            for row in norms:
                norm_by_check[row["check_name"]].append(row)
                if row.get("poll_id") is not None:
                    norm_by_poll[row["poll_id"]] = row
            poll_hashes = {}
            norm_hashes = {}
            for check_name in sorted(by_check):
                poll_hashes[check_name], _ = _verify_chain(
                    by_check[check_name], _poll_fields, POLL_CHAIN_VERSION)
                norm_hashes[check_name], _ = _verify_chain(
                    norm_by_check[check_name], _norm_fields,
                    NORMALISATION_CHAIN_VERSION)
            annotation_hashes, annotation_head = _verify_chain(
                annotations, _annotation_fields, ANNOTATION_CHAIN_VERSION)
            del anchors  # Anchor files are read by _anchor_state as needed.

            source_items = []
            observation_files = {}
            all_records = {}
            all_entities = {}
            all_events = []
            check_snapshot_maps = {}
            successful_by_check = {}
            generated_values = [row["polled_at"] for row in polls]

            for check_name in sorted(by_check):
                definition = SOURCE_DEFINITIONS.get(check_name, {
                    "slug": _slug(check_name), "source_type": "unknown",
                    "category": "unclassified", "extractor": None,
                    "configured_url": None,
                })
                source_id = definition["slug"]
                is_control = check_name in {
                    row["check_name"] for row in annotations
                    if row["kind"] == "note" and row.get("check_name")
                    and _annotation_detail(row).get("role") == "control"
                }
                if is_control and not include_control:
                    continue
                previous_digest = None
                previous_observation = None
                previous_records = {}
                seen_records = set()
                record_first = {}
                record_change_times = defaultdict(list)
                observations = []
                events = []
                successful = []
                for sequence, poll in enumerate(by_check[check_name], 1):
                    digest = poll.get("content_sha256")
                    body = None
                    current_records = {}
                    if poll["ok"] and digest:
                        body = archive.blob(digest)
                        current_records = _extract_structured(
                            body, definition, source_id)
                        successful.append(poll)
                    norm = norm_by_poll.get(poll["id"])
                    anchor = _anchor_state(
                        archive, poll, poll_hashes[check_name],
                        norm_hashes[check_name], annotation_hashes)
                    observation_id = f"{source_id}:poll:{poll['id']}"
                    record_ids = sorted(current_records)
                    observation = {
                        "id": observation_id,
                        "target_id": source_id,
                        "sequence": sequence,
                        "observed_at": poll["polled_at"],
                        "source": {"name": check_name,
                                   "url": poll.get("url")},
                        "result": {
                            "success": bool(poll["ok"]),
                            "http_status": poll.get("http_status"),
                            "content_length": poll.get("content_length"),
                            "etag": poll.get("etag"),
                            "last_modified": poll.get("last_modified"),
                            "error": poll.get("error"),
                        },
                        "capture": {
                            "content_sha256": digest,
                            "previous_content_sha256": previous_digest,
                            "changed": bool(poll["changed"]) if digest else None,
                            "retained": bool(body is not None),
                            "public_copy": None,
                        },
                        "normalisation": None if norm is None else {
                            "id": f"{source_id}:normalisation:{norm['id']}",
                            "content_sha256": norm["content_sha256"],
                            "previous_content_sha256": _previous_norm_digest(
                                norm_by_check[check_name], norm["id"]),
                            "changed": bool(norm["changed"]),
                            "transform_id": norm["transform_id"],
                            "record_hash": norm["record_hash"],
                        },
                        "record_ids": record_ids,
                        "chain": {
                            "status": "sound",
                            "previous_entry_sha256": poll["prev_hash"],
                            "entry_sha256": poll["record_hash"],
                            "head_sha256": poll_hashes[check_name][poll["id"]],
                        },
                        "anchor": anchor,
                    }
                    observations.append(observation)

                    if previous_observation and poll["ok"] and digest:
                        if digest != previous_digest:
                            events.append(_change_event(
                                source_id, observation_id,
                                previous_observation["id"], poll["polled_at"],
                                "raw_response_changed", {
                                    "mode": "raw_response",
                                    "old": previous_digest,
                                    "new": digest,
                                }))
                        if norm and norm["changed"] and previous_norm_digest(
                                norm_by_check[check_name], norm["id"]):
                            events.append(_change_event(
                                source_id, observation_id,
                                previous_observation["id"], poll["polled_at"],
                                "content_changed", {
                                    "mode": "normalised_document",
                                    "old": previous_norm_digest(
                                        norm_by_check[check_name], norm["id"]),
                                    "new": norm["content_sha256"],
                                }))
                        for record_id in sorted(set(previous_records) | set(current_records)):
                            old = previous_records.get(record_id)
                            new = current_records.get(record_id)
                            if old is None and new is not None:
                                pass
                            elif old is not None and new is None:
                                event = _change_event(
                                    source_id, observation_id,
                                    previous_observation["id"], poll["polled_at"],
                                    "disappeared", {
                                        "mode": "structured_record",
                                        "interpretation": "record absent from a later deterministic source projection; not evidence of withdrawal",
                                        "old": old,
                                        "new": None,
                                    }, record_id, old.get("title"))
                                events.append(event)
                                record_change_times[record_id].append(poll["polled_at"])
                            elif old is not None and new is not None:
                                for event_type, comparison in _compare_record(old, new):
                                    events.append(_change_event(
                                        source_id, observation_id,
                                        previous_observation["id"], poll["polled_at"],
                                        event_type, comparison, record_id,
                                        new.get("title")))
                                    record_change_times[record_id].append(poll["polled_at"])
                    for record_id, snapshot in sorted(current_records.items()):
                        if record_id not in seen_records:
                            first_event = _change_event(
                                source_id, observation_id, None, poll["polled_at"],
                                "first_seen", {"mode": "record", "new": snapshot},
                                record_id, snapshot.get("title"))
                            events.append(first_event)
                            seen_records.add(record_id)
                            record_first[record_id] = poll["polled_at"]
                        for entity in [snapshot.get("buyer"), snapshot.get("supplier")]:
                            if entity:
                                all_entities[entity["id"]] = {
                                    **entity, "record_ids": sorted(set(
                                        all_entities.get(entity["id"], {}).get(
                                            "record_ids", []) + [record_id]))}
                        for entity in snapshot.get("suppliers", []):
                            all_entities[entity["id"]] = {
                                **entity, "record_ids": sorted(set(
                                    all_entities.get(entity["id"], {}).get(
                                        "record_ids", []) + [record_id]))}
                        existing = all_records.get(record_id)
                        if existing is None:
                            all_records[record_id] = {
                                "id": record_id, **snapshot,
                                "dates": {**snapshot["dates"],
                                          "first_observed": poll["polled_at"],
                                          "last_observed": poll["polled_at"],
                                          "first_observed_changed": None},
                                "source": {"target_id": source_id,
                                           "name": check_name,
                                           "url": poll.get("url"),
                                           **snapshot["source"]},
                                "evidence": {"observation_ids": [observation_id],
                                             "observation_count": 1},
                            }
                        else:
                            existing.update({
                                key: value for key, value in snapshot.items()
                                if key not in ("source", "dates")
                            })
                            existing["dates"].update({
                                key: value for key, value in snapshot["dates"].items()
                                if value is not None})
                            existing["dates"]["last_observed"] = poll["polled_at"]
                            existing["source"].update(snapshot["source"])
                            existing["evidence"]["observation_ids"].append(observation_id)
                            existing["evidence"]["observation_count"] += 1
                    if poll["ok"] and digest:
                        previous_digest = digest
                        previous_observation = observation
                        previous_records = current_records
                    elif poll["ok"] and not digest:
                        previous_observation = observation
                    events = sorted(events, key=lambda item: (
                        item["first_observed_at"], item["id"]))
                    all_events.extend(events)
                    events = []
                for record_id, record in all_records.items():
                    if record["source"]["target_id"] != source_id:
                        continue
                    changes = record_change_times.get(record_id, [])
                    record["dates"]["first_observed_changed"] = _first_time(changes)
                successful_by_check[check_name] = successful
                observation_files[source_id] = {
                    "schema_version": SCHEMA_VERSION,
                    "target_id": source_id,
                    "observations": observations,
                }
                source_url = definition.get("configured_url") or next(
                    (row.get("url") for row in reversed(successful) if row.get("url")),
                    None)
                coverage_counts = defaultdict(int)
                for item in observations:
                    coverage_counts[item["anchor"]["status"]] += 1
                latest_observation = observations[-1]
                source_items.append({
                    "id": source_id,
                    "name": check_name,
                    "kind": "control" if is_control else "source",
                    "source_type": definition["source_type"],
                    "category": definition["category"],
                    "url": source_url,
                    "configured_url": definition.get("configured_url"),
                    "observation_count": len(by_check[check_name]),
                    "successful_observation_count": len(successful),
                    "last_observed_at": _max_time(
                        [row["polled_at"] for row in successful]),
                    "first_observed_at": _first_time(
                        [row["polled_at"] for row in successful]),
                    "last_observation_attempt_at": _max_time(
                        [row["polled_at"] for row in by_check[check_name]]),
                    "raw_change_count": sum(bool(row["changed"]) for row in by_check[check_name]),
                    "document_change_count": sum(bool(row["changed"]) for row in norm_by_check[check_name]),
                    "recorded_change_count": sum(
                        item["event_type"] != "first_seen" for item in all_events
                        if item["target_id"] == source_id),
                    "latest_recorded_change_at": _max_time([
                        item["first_observed_at"] for item in all_events
                        if item["target_id"] == source_id
                        and item["event_type"] != "first_seen"]),
                    "chain_status": "sound",
                    "chain_heads": {
                        "poll": poll_hashes[check_name][by_check[check_name][-1]["id"]],
                        "normalisation": (
                            norm_hashes[check_name].get(
                                norm_by_check[check_name][-1]["id"], GENESIS)
                            if norm_by_check[check_name] else GENESIS),
                        "annotation": annotation_head,
                    },
                    "timestamp_coverage": dict(sorted(coverage_counts.items())),
                    "latest_timestamp_status": latest_observation["anchor"]["status"],
                    "record_count": sum(
                        1 for record in all_records.values()
                        if record["source"]["target_id"] == source_id),
                })

            all_events = sorted({item["id"]: item for item in all_events}.values(),
                                key=lambda item: (item["first_observed_at"], item["id"]))
            for record in all_records.values():
                record["evidence"]["observation_ids"] = sorted(
                    record["evidence"]["observation_ids"],
                    key=lambda value: int(value.rsplit(":", 1)[-1]))
            source_items = sorted(source_items, key=lambda item: item["id"])
            records = sorted(all_records.values(), key=lambda item: item["id"])
            entities = sorted(all_entities.values(), key=lambda item: item["id"])
            changes = all_events
            government_sources = [item for item in source_items
                                  if item["kind"] != "control"]
            government_obs = sum(item["observation_count"] for item in government_sources)
            government_changes = sum(item["recorded_change_count"] for item in government_sources)
            generated_at = _max_time(generated_values)
            if generated_at is None:
                generated_at = "1970-01-01T00:00:00+00:00"

            _write_json(temporary, "sources.json", {
                "schema_version": SCHEMA_VERSION, "sources": source_items})
            _write_json(temporary, "entities.json", {
                "schema_version": SCHEMA_VERSION, "entities": entities})
            _write_json(temporary, "records.json", {
                "schema_version": SCHEMA_VERSION, "records": records})
            _write_json(temporary, "changes.json", {
                "schema_version": SCHEMA_VERSION, "changes": changes})
            for source_id, payload in sorted(observation_files.items()):
                _write_json(temporary, f"observations/{source_id}.json", payload)
            for record in records:
                _write_json(temporary, f"records/{_record_file_id(record['id'])}.json", record)

            files = []
            for path in sorted(temporary.rglob("*.json")):
                relative = path.relative_to(temporary).as_posix()
                files.append({"path": relative, "sha256": sha256_hex(path.read_bytes()),
                              "bytes": path.stat().st_size})
            export_basis = {"schema_version": SCHEMA_VERSION,
                            "generated_at": generated_at,
                            "files": files,
                            "chain_heads": [item["chain_heads"] for item in source_items]}
            export_id = "export-" + sha256_hex(_canonical(export_basis))[:32]
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "export_id": export_id,
                "generated_at": generated_at,
                "producer": {"name": "kibitzr-archive-public-export",
                              "git_commit": _producer_commit(),
                              "package_version": __version__},
                "source_collector_id": _collector_id(annotations),
                "target_count": len(source_items),
                "observation_count": sum(item["observation_count"] for item in source_items),
                "recorded_change_count": sum(
                    item["event_type"] != "first_seen" for item in changes),
                "summary": {
                    "government_sources": len(government_sources),
                    "control_checks": len(source_items) - len(government_sources),
                    "government_observations": government_obs,
                    "recorded_changes": government_changes,
                    "timestamp_coverage_counts": _coverage_summary(
                        source_items, government_only=True),
                },
                "per_target_chain_heads": [
                    {"target_id": item["id"], **item["chain_heads"]}
                    for item in source_items],
                "files": files,
            }
            _write_json(temporary, "manifest.json", manifest)
            _validate_output(temporary)

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


def _annotation_detail(row):
    try:
        return json.loads(row.get("detail") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _collector_id(annotations):
    for row in reversed(annotations):
        detail = _annotation_detail(row)
        if detail.get("role") == "collector_instance":
            return detail.get("instance_id")
    return None


def _previous_norm_digest(rows, row_id):
    previous = [row for row in rows if row["id"] < row_id]
    return previous[-1]["content_sha256"] if previous else None


def previous_norm_digest(rows, row_id):
    return _previous_norm_digest(rows, row_id)


def _coverage_summary(source_items, government_only=False):
    counts = defaultdict(int)
    for source in source_items:
        if government_only and source["kind"] == "control":
            continue
        for status, count in source["timestamp_coverage"].items():
            counts[status] += count
    return dict(sorted(counts.items()))


def export_public(archive_root, output, **kwargs):
    """Public API alias used by tests and integrations."""
    return build_export(archive_root, output, **kwargs)
