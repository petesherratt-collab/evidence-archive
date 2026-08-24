#!/usr/bin/env python3
"""Independently verify the public projection of an evidence archive.

This verifier deliberately imports neither the public exporter nor any of its
transformation helpers.  It reconstructs the projection specification from
SQLite and retained response bytes, then compares the resulting universe and
event stream with the published export.  Hashes and manifest arithmetic are
necessary, but they are not a completeness proof: the archive-derived sets
below are the authority for that claim.

    python3 deploy/verify_public_export_independently.py ARCHIVE EXPORT
    python3 deploy/verify_public_export_independently.py --historical ARCHIVE EXPORT
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path


GENESIS = "0" * 64
SCHEMA_VERSION = "1.0.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


SOURCE_DEFINITIONS = {
    "Contracts Finder — recent awards": {
        "source_type": "ocds_api",
        "category": "government_contracts",
        "extractor": "ocds_awards",
        "configured_url": "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?stages=award",
    },
    "Find a Tender — recent awards": {
        "source_type": "ocds_api",
        "category": "government_contracts",
        "extractor": "ocds_awards",
        "configured_url": "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages",
    },
    "Government Commercial Agency — agreements": {
        "source_type": "html_publication",
        "category": "frameworks",
        "extractor": None,
        "configured_url": "https://www.gca.gov.uk/agreements",
    },
    "Departmental spend over £25k — monthly release": {
        "source_type": "html_publication",
        "category": "government_spending",
        "extractor": None,
        "configured_url": "https://www.gov.uk/government/collections/dhsc-spending-over-25000",
    },
    "UK direct awards — no competition": {
        "source_type": "ocds_api",
        "category": "government_contracts",
        "extractor": "ocds_direct_awards",
        "configured_url": "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?stages=award",
    },
}


def public_json(value):
    """Canonical bytes used by the public JSON files and public identities."""
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("ascii")


def chain_json(value):
    """Canonical bytes used by the archive's SQLite hash chains."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def slug(value):
    value = unicodedata.normalize("NFKD", value).encode(
        "ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-") or "source"


def record_file_id(record_id):
    return hashlib.sha256(record_id.encode()).hexdigest()[:24]


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def max_time(values):
    parsed = [(item, parse_time(item)) for item in values if item]
    parsed = [(item, value) for item, value in parsed if value is not None]
    return max(parsed, key=lambda pair: pair[1])[0] if parsed else None


def first_time(values):
    parsed = [(item, parse_time(item)) for item in values if item]
    parsed = [(item, value) for item, value in parsed if value is not None]
    return min(parsed, key=lambda pair: pair[1])[0] if parsed else None


def unique(values):
    result = []
    for value in values:
        if value is not None and value not in result:
            result.append(value)
    return result


def consistent(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return values[0] if all(value == values[0] for value in values[1:]) else None


class Archive:
    """Small read-only archive view with independently checked blobs."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.db_path = self.root / "polls.db"
        if not self.db_path.is_file():
            raise ValueError(f"no archive database at {self.db_path}")
        uri = "file:" + str(self.db_path).replace("%", "%25") + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def rows(self, table):
        return [dict(row) for row in self.conn.execute(
            f"SELECT * FROM {table} ORDER BY id")]

    def blob(self, digest):
        if not digest or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"invalid retained content digest {digest!r}")
        path = self.root / "blobs" / digest[:2] / (digest + ".gz")
        if not path.is_file():
            raise ValueError(f"retained blob is missing for {digest}")
        with gzip.open(path, "rb") as handle:
            body = handle.read()
        actual = sha256(body)
        if actual != digest:
            raise ValueError(
                f"retained blob {digest} hashes to {actual}; refusing export")
        return body


LEGACY_CONTROL_EVENT_EXPORT_ID = (
    "export-adc53ae73a759cd128df9575be0cb759"
)


class HistoricalCut:
    """Authenticated archive boundary derived from a published snapshot."""
    def __init__(
            self, poll_ids, norm_ids, annotation_id, annotation_head,
            collector_id, generated_at, archive_latest_poll,
            later_poll_count, selected_poll_count, legacy_control_events=False):
        self.poll_ids = dict(poll_ids)
        self.norm_ids = dict(norm_ids)
        self.annotation_id = annotation_id
        self.annotation_head = annotation_head
        self.collector_id = collector_id
        self.generated_at = generated_at
        self.archive_latest_poll = archive_latest_poll
        self.later_poll_count = later_poll_count
        self.selected_poll_count = selected_poll_count
        self.legacy_control_events = bool(legacy_control_events)
        self.max_poll_id = max(self.poll_ids.values())


def chain_hash(fields, previous, version=1):
    return sha256(chain_json(dict(fields, v=version, prev=previous)))


def poll_fields(row):
    fields = {
        "check": row["check_name"],
        "url": row["url"],
        "polled_at": row["polled_at"],
        "ok": bool(row["ok"]),
        "http_status": row["http_status"],
        "content_sha256": row["content_sha256"],
        "changed": bool(row["changed"]),
    }
    if row.get("fetch_id") is not None:
        fields["fetch_id"] = row["fetch_id"]
    return fields


def normalisation_fields(row):
    return {
        "check": row["check_name"],
        "poll_id": row["poll_id"],
        "recorded_at": row["recorded_at"],
        "content_sha256": row["content_sha256"],
        "transform_id": row["transform_id"],
        "changed": bool(row["changed"]),
    }


def annotation_fields(row):
    return {
        "kind": row["kind"],
        "check": row["check_name"],
        "effective_from": row["effective_from"],
        "recorded_at": row["recorded_at"],
        "subject_from": row["subject_from"],
        "subject_to": row["subject_to"],
        "detail": row["detail"],
    }


def rebuild_chain(rows, fields, label, errors):
    previous = GENESIS
    hashes = {}
    for row in rows:
        expected = chain_hash(fields(row), previous)
        if row.get("prev_hash") != previous:
            errors.append(f"{label} row {row['id']} has an invalid previous hash")
        if row.get("record_hash") != expected:
            errors.append(f"{label} row {row['id']} has an invalid record hash")
        hashes[row["id"]] = expected
        previous = expected
    return hashes, previous


def detail(row):
    try:
        value = json.loads(row.get("detail") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def is_control(check_name, annotations):
    return any(row["kind"] == "note" and row["check_name"] == check_name
               and detail(row).get("role") == "control"
               for row in annotations)


def collector_id(annotations):
    for row in reversed(annotations):
        if detail(row).get("role") == "collector_instance":
            return detail(row).get("instance_id")
    return None


OTS_MAGIC = (b"\x00OpenTimestamps\x00\x00Proof\x00"
             b"\xbf\x89\xe2\xe8\x84\xe8\x92\x94")
OTS_HASH_OPS = {0x02: 20, 0x03: 20, 0x08: 32, 0x67: 32}


def ots_committed_digest(raw):
    if not raw.startswith(OTS_MAGIC):
        return None
    offset = len(OTS_MAGIC)
    while offset < len(raw) and raw[offset] & 0x80:
        offset += 1
    offset += 1
    if offset >= len(raw):
        return None
    length = OTS_HASH_OPS.get(raw[offset])
    if length is None:
        return None
    digest = raw[offset + 1:offset + 1 + length]
    return digest.hex() if len(digest) == length else None


def anchor_state(archive, poll, poll_hashes, norm_hashes, annotation_hashes,
                 cut=None):
    """Rebuild the conservative observation anchor state independently."""
    candidates = []
    invalid = False
    for row in archive.conn.execute(
            "SELECT * FROM anchor WHERE check_name = ? ORDER BY id",
            (poll["check_name"],)):
        row = dict(row)
        if row["status"] == "failed" or not row.get("proof_ref"):
            continue
        if cut is not None:
            if (row.get("last_poll_id") is None
                    or row["last_poll_id"] > cut.poll_ids[poll["check_name"]]):
                continue
            anchored_at = parse_time(row.get("anchored_at"))
            cut_at = parse_time(cut.generated_at)
            if anchored_at is None or cut_at is None or anchored_at > cut_at:
                continue
        manifest_path = archive.root / row["manifest_ref"]
        proof_path = archive.root / row["proof_ref"]
        if not manifest_path.is_file() or not proof_path.is_file():
            invalid = True
            continue
        try:
            manifest_bytes = manifest_path.read_bytes()
            if sha256(manifest_bytes) != row["manifest_sha256"]:
                invalid = True
                continue
            covered = ots_committed_digest(proof_path.read_bytes())
            if covered != row["manifest_sha256"]:
                invalid = True
                continue
            manifest = json.loads(manifest_bytes)
            entry = next((item for item in manifest.get("checks", [])
                          if item.get("check") == poll["check_name"]), None)
            if not entry or (entry.get("last_poll_id") or 0) < poll["id"]:
                continue
            last_id = entry.get("last_poll_id")
            if poll_hashes.get(last_id) != entry.get("poll_head"):
                continue
            if entry.get("norm_head") not in set(norm_hashes) | {GENESIS}:
                continue
            if entry.get("annotation_head") not in set(annotation_hashes) | {GENESIS}:
                continue
            status = "bitcoin-backed" if row["status"] == "complete" else "pending"
            candidates.append((last_id, status, entry, row))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            invalid = True
    if not candidates:
        if invalid:
            return {"status": "verification-failed", "coverage": "verification-failed"}
        return {"status": "none", "coverage": "awaiting-coverage"}
    rank = {"none": 0, "pending": 1, "bitcoin-backed": 2}
    _last_id, status, entry, anchor = max(
        candidates, key=lambda item: (item[0], rank[item[1]]))
    return {
        "status": status,
        "coverage": "covered",
        "manifest_sha256": anchor["manifest_sha256"],
        "submitted_at": anchor["anchored_at"],
        "manifest_ref": anchor["manifest_ref"],
        "proof_ref": anchor["proof_ref"],
        "head_sha256": entry["combined_head"],
    }


def entity(role, raw):
    if not isinstance(raw, dict):
        return None
    identifier = raw.get("id")
    legal = raw.get("identifier")
    legal_name = legal.get("legalName") if isinstance(legal, dict) else None
    name = raw.get("name") or legal_name
    if not name and not identifier:
        return None
    value = identifier or name
    return {"id": f"entity:{role}:{value}", "name": name, "role": role,
            "source_identifier": identifier}


def party_for_role(parties, role, preferred=None):
    direct = entity(role, preferred if isinstance(preferred, dict) else {})
    if direct:
        return direct
    for party in parties or []:
        if role in (party.get("roles") or []):
            found = entity(role, party)
            if found:
                return found
    return None


def cpv_values(tender):
    values = []
    classifications = [tender.get("classification") or {}]
    classifications.extend(
        classification
        for item in (tender.get("items") or [])
        for classification in (item.get("additionalClassifications") or []))
    for item in classifications:
        if item.get("scheme", "").upper() == "CPV" and item.get("id"):
            values.append({"code": str(item["id"]),
                           "description": item.get("description")})
    distinct = {json.dumps(item, sort_keys=True): item for item in values}
    return sorted(distinct.values(), key=lambda item: item["code"])


def record_snapshot(release, source_id):
    tender = release.get("tender") or {}
    parties = release.get("parties") or []
    awards = [item for item in (release.get("awards") or [])
              if isinstance(item, dict)]
    if not release.get("ocid") and not release.get("id"):
        return None
    source_key = release.get("ocid") or release.get("id")
    record_id = f"record:{source_id}:{source_key}"
    buyer = party_for_role(parties, "buyer", release.get("buyer"))
    suppliers = []
    for award in awards:
        for supplier in award.get("suppliers") or []:
            found = entity("supplier", supplier)
            if found and found not in suppliers:
                suppliers.append(found)
    if not suppliers:
        for party in parties:
            if "supplier" in (party.get("roles") or []):
                found = entity("supplier", party)
                if found and found not in suppliers:
                    suppliers.append(found)
    award_values = [award.get("value") for award in awards]
    value = consistent(award_values) or (
        tender.get("value") if not awards else None)
    award_dates = [award.get("date") for award in awards]
    award_date = consistent(award_dates)
    published = release.get("publishedDate")
    if not published:
        published = consistent([
            document.get("datePublished")
            for award in awards
            for document in (award.get("documents") or [])])
    if not published:
        published = release.get("date")
    periods = [award.get("contractPeriod") for award in awards]
    if not periods:
        periods = [tender.get("contractPeriod")]
    starts = consistent([period.get("startDate") for period in periods if period])
    ends = consistent([period.get("endDate") for period in periods if period])
    classifications = cpv_values(tender)
    statuses = [award.get("status") for award in awards]
    status = consistent(statuses) or (tender.get("status") if not awards else None)
    notice_urls = [
        document.get("url") for award in awards
        for document in (award.get("documents") or []) if document.get("url")
    ] + [document.get("url") for document in (release.get("documents") or [])
         if document.get("url")]
    tags = release.get("tag") or []
    kind = "award" if awards or "award" in tags or "awardUpdate" in tags \
        else "procurement"
    return record_id, {
        "id": record_id,
        "type": kind,
        "title": tender.get("title"),
        "buyer": buyer,
        "supplier": suppliers[0] if len(suppliers) == 1 else None,
        "suppliers": suppliers,
        "notice_type": tags[0] if tags else None,
        "dates": {"published": published, "award": award_date,
                  "start": starts, "end": ends},
        "value": value if isinstance(value, dict) else None,
        "classification": {"cpv": classifications,
                            "category": consistent(
                                [item.get("description") for item in classifications])},
        "status": status,
        "source": {"notice_url": notice_urls[0] if notice_urls else None},
    }


def extract_structured(body, definition, source_id):
    if not definition.get("extractor"):
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    releases = payload.get("releases") if isinstance(payload, dict) else None
    if not isinstance(releases, list):
        return {}
    result = {}
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
        found = record_snapshot(release, source_id)
        if found:
            result[found[0]] = found[1]
    return result


def event_id(event):
    body = dict(event)
    body.pop("id", None)
    return "event:" + sha256(public_json(body))[:24]


def event(target_id, observation_id, previous_observation_id, observed_at,
          event_type, comparison, record_id=None, title=None):
    result = {
        "target_id": target_id,
        "observation_id": observation_id,
        "previous_observation_id": previous_observation_id,
        "first_observed_at": observed_at,
        "record_id": record_id,
        "title": title,
        "event_type": event_type,
        "comparison": comparison,
    }
    result["id"] = event_id(result)
    return result


def compare_record(previous, current):
    for field, event_type in (("value", "value_changed"),
                              ("dates", "date_changed"),
                              ("suppliers", "supplier_changed"),
                              ("status", "status_changed")):
        if previous.get(field) != current.get(field):
            yield event_type, {"mode": "structured_field", "field": field,
                               "old": previous.get(field),
                               "new": current.get(field)}


def previous_norm_digest(rows, row_id):
    prior = [row for row in rows if row["id"] < row_id]
    return prior[-1]["content_sha256"] if prior else None


def _rows_for_cut(rows, cut, limits):
    if cut is None:
        return rows
    return [row for row in rows
            if row["id"] <= limits.get(row["check_name"], -1)]


def _annotations_for_cut(rows, cut):
    if cut is None:
        return rows
    return [row for row in rows if row["id"] <= cut.annotation_id]


def authenticate_chain_prefix(rows, fields, head, label, errors):
    """Find a named head and verify every chain row up to it.

    Rows after the named head are deliberately outside this operation.  A
    historical verifier must prove the prefix before it is allowed to ignore
    later archive rows; ordinary verification still rebuilds the entire live
    chain through ``build_projection``.
    """
    if head == GENESIS:
        return None
    matches = [row for row in rows if row.get("record_hash") == head]
    if len(matches) != 1:
        if not matches:
            errors.append(f"{label} head is not present in the live archive")
        else:
            errors.append(f"{label} head is not unique in the live archive")
        return None
    row = matches[0]
    prefix = [item for item in rows if item["id"] <= row["id"]]
    _hashes, recomputed = rebuild_chain(prefix, fields, label, errors)
    if recomputed != head:
        errors.append(f"{label} head does not match the authenticated prefix")
    return row


def validate_chain_suffix(rows, fields, previous, label, errors):
    """Validate the rows appended after an already authenticated head."""
    for row in rows:
        expected = chain_hash(fields(row), previous)
        if row.get("prev_hash") != previous:
            errors.append(f"{label} row {row['id']} has an invalid previous hash")
        if row.get("record_hash") != expected:
            errors.append(f"{label} row {row['id']} has an invalid record hash")
        previous = expected
    return previous


def _observation_poll_id(observation_id, target_id):
    prefix = f"{target_id}:poll:"
    if not isinstance(observation_id, str) or not observation_id.startswith(prefix):
        return None
    try:
        return int(observation_id[len(prefix):])
    except (TypeError, ValueError):
        return None


def validate_exported_observation_heads(exported_observations, entries,
                                        target_rows, errors):
    """Use the published observation tails as additional cut evidence."""
    if exported_observations is None:
        return
    entry_targets = {entry.get("target_id") for entry in entries
                     if isinstance(entry, dict)}
    if set(exported_observations) != entry_targets:
        missing = sorted(entry_targets - set(exported_observations))
        extra = sorted(set(exported_observations) - entry_targets)
        if missing:
            errors.append("historical export is missing observation files for: "
                          + ", ".join(missing))
        if extra:
            errors.append("historical export has unexpected observation files for: "
                          + ", ".join(extra))

    for target_id, poll_row, poll_head in target_rows:
        payload = exported_observations.get(target_id)
        if not isinstance(payload, dict):
            continue
        if payload.get("target_id") != target_id:
            errors.append(f"historical observations for {target_id} have the wrong target")
        observations = payload.get("observations")
        if not isinstance(observations, list) or not observations:
            errors.append(f"historical observations for {target_id} are empty")
            continue
        final = observations[-1]
        final_id = final.get("id") if isinstance(final, dict) else None
        final_poll_id = _observation_poll_id(final_id, target_id)
        if final_poll_id != poll_row["id"]:
            errors.append(
                f"historical {target_id} observation tail does not name its poll head")
        if isinstance(final, dict):
            chain = final.get("chain") or {}
            if chain.get("entry_sha256") != poll_head:
                errors.append(
                    f"historical {target_id} observation tail does not carry its poll head")


def derive_historical_cut(archive, manifest, errors,
                          exported_observations=None):
    """Derive a cut exclusively from chain heads published in the snapshot."""
    if not isinstance(manifest, dict):
        errors.append("manifest.json is required for historical verification")
        return None
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("manifest.json has unsupported schema for historical verification")
    entries = manifest.get("per_target_chain_heads")
    if not isinstance(entries, list) or not entries:
        errors.append("manifest.per_target_chain_heads is required for historical verification")
        return None

    export_id = manifest.get("export_id")
    if not isinstance(export_id, str) or not re.fullmatch(
            r"export-[0-9a-f]{32}", export_id):
        errors.append("manifest.export_id is not a valid historical export identity")

    polls_all = archive.rows("poll")
    norms_all = archive.rows("normalisation")
    annotations_all = archive.rows("annotation")
    by_check = defaultdict(list)
    for row in polls_all:
        by_check[row["check_name"]].append(row)

    # Authenticate the global annotation head before using annotations to
    # decide which non-source poll series are controls.  A control declaration
    # appended after the historical head must not retroactively make an older
    # unsupported series eligible.
    entries = manifest.get("per_target_chain_heads")
    annotation_heads = {
        entry.get("annotation") for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("annotation"), str)
        and SHA256_RE.fullmatch(entry.get("annotation"))
    }
    annotation_head = next(iter(annotation_heads), None)
    if len(annotation_heads) != 1:
        errors.append("historical target chain heads do not agree on annotation head")
        annotation_id = 0
        annotation_prefix = []
    else:
        annotation_row = authenticate_chain_prefix(
            annotations_all, annotation_fields, annotation_head,
            "annotation", errors)
        annotation_id = (annotation_row["id"]
                         if annotation_row is not None else 0)
        annotation_prefix = [row for row in annotations_all
                             if row["id"] <= annotation_id]
        if annotation_head == GENESIS:
            annotation_prefix = []
        validate_chain_suffix(
            [row for row in annotations_all if row["id"] > annotation_id],
            annotation_fields, annotation_head if annotation_head else GENESIS,
            "annotation", errors)

    known_checks = {
        check_name for check_name in by_check
        if check_name in SOURCE_DEFINITIONS or is_control(check_name, annotation_prefix)
    }
    poll_ids = {}
    norm_ids = {}
    seen_targets = set()
    target_rows = []

    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("manifest.per_target_chain_heads contains a non-object")
            continue
        target_id = entry.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            errors.append("historical chain head has no target_id")
            continue
        if target_id in seen_targets:
            errors.append(f"duplicated historical target chain head: {target_id}")
            continue
        seen_targets.add(target_id)
        candidates = [check_name for check_name in known_checks
                      if slug(check_name) == target_id]
        if len(candidates) != 1:
            errors.append(f"historical target is not an eligible live check: {target_id}")
            continue
        check_name = candidates[0]
        poll_head = entry.get("poll")
        norm_head = entry.get("normalisation")
        entry_annotation_head = entry.get("annotation")
        for label, value in (("poll", poll_head),
                             ("normalisation", norm_head),
                             ("annotation", entry_annotation_head)):
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                errors.append(f"historical {target_id} {label} head is not a SHA-256 digest")
        if not all(isinstance(value, str) and SHA256_RE.fullmatch(value)
                   for value in (poll_head, norm_head, entry_annotation_head)):
            continue
        if entry_annotation_head != annotation_head:
            errors.append(
                f"historical {target_id} annotation head differs from the global head")

        poll_row = authenticate_chain_prefix(
            by_check[check_name], poll_fields, poll_head,
            f"poll[{check_name}]", errors)
        if poll_row is None:
            continue
        poll_ids[check_name] = poll_row["id"]
        target_rows.append((target_id, poll_row, poll_head))

        norm_rows = [row for row in norms_all if row["check_name"] == check_name]
        norm_row = authenticate_chain_prefix(
            norm_rows, normalisation_fields, norm_head,
            f"normalisation[{check_name}]", errors)
        if norm_head == GENESIS:
            if any(row.get("poll_id") is not None
                   and row["poll_id"] <= poll_row["id"] for row in norm_rows):
                errors.append(
                    f"normalisation[{check_name}] claims an empty authenticated prefix"
                    " despite eligible rows before its poll head")
        elif norm_row is not None:
            if norm_row.get("poll_id") is None or norm_row["poll_id"] > poll_row["id"]:
                errors.append(
                    f"normalisation[{check_name}] head is beyond its authenticated poll state")
            norm_ids[check_name] = norm_row["id"]

    if not poll_ids:
        errors.append("historical verification found no authenticated poll heads")
        return None
    max_poll_id = max(poll_ids.values())

    # The largest authenticated target head defines the global cut.  Every
    # eligible row at or before that global ID must be represented by that
    # target's head; merely listing a target's older head is not a legitimate
    # way to skip a poll that was already in the cut.
    for check_name in sorted(known_checks):
        rows_at_cut = [row for row in by_check[check_name]
                       if row["id"] <= max_poll_id]
        if rows_at_cut and check_name not in poll_ids:
            errors.append(
                f"historical snapshot omits the chain head for eligible check {check_name}")
        elif rows_at_cut and poll_ids.get(check_name) != rows_at_cut[-1]["id"]:
            errors.append(
                f"historical {check_name} chain head skips an eligible poll at the cut")

    validate_exported_observation_heads(
        exported_observations, entries, target_rows, errors)

    # Authenticate the post-cut suffixes as append-only continuations.  The
    # historical projection will not consume these rows, but a verifier must
    # not silently ignore a broken or rewritten continuation.
    for check_name, rows in sorted(by_check.items()):
        if check_name in poll_ids:
            boundary = poll_ids[check_name]
            boundary_row = next(row for row in rows if row["id"] == boundary)
            validate_chain_suffix(
                [row for row in rows if row["id"] > boundary], poll_fields,
                boundary_row["record_hash"], f"poll[{check_name}]", errors)
        else:
            rebuild_chain(rows, poll_fields, f"poll[{check_name}]", errors)

    norms_by_check = defaultdict(list)
    for row in norms_all:
        norms_by_check[row["check_name"]].append(row)
    for check_name, rows in sorted(norms_by_check.items()):
        boundary = norm_ids.get(check_name, 0)
        boundary_hash = GENESIS
        if boundary:
            boundary_hash = next(row for row in rows if row["id"] == boundary)["record_hash"]
        validate_chain_suffix(
            [row for row in rows if row["id"] > boundary], normalisation_fields,
            boundary_hash, f"normalisation[{check_name}]", errors)

    expected_poll_keys = {
        (check_name, row["id"])
        for check_name in known_checks
        for row in by_check[check_name]
        if row["id"] <= max_poll_id
    }
    actual_poll_keys = {
        (check_name, row["id"])
        for check_name in poll_ids
        for row in by_check[check_name]
        if row["id"] <= max_poll_id
    }
    if expected_poll_keys != actual_poll_keys:
        errors.append("historical authenticated poll prefix is not an exact eligible set")

    selected_polls = [row for check_name, limit in poll_ids.items()
                      for row in by_check[check_name] if row["id"] <= limit]
    generated_at = max_time([row["polled_at"] for row in selected_polls])
    if generated_at is None:
        errors.append("historical authenticated state has no valid poll timestamp")
        return None
    manifest_generated_at = manifest.get("generated_at")
    if parse_time(manifest_generated_at) is None:
        errors.append("manifest.generated_at is not a valid timestamp")
    elif manifest_generated_at != generated_at:
        errors.append(
            "manifest.generated_at does not match the authenticated historical state")
    cut_time = parse_time(generated_at)
    if cut_time is not None:
        # A normalisation row linked to a poll in the historical prefix and
        # recorded by the cut must not be skipped by declaring an older valid
        # normalisation head.  Rows appended later remain valid suffix history,
        # even if they happen to re-normalise an older poll.
        for check_name, poll_limit in poll_ids.items():
            norm_rows = [row for row in norms_all
                         if row["check_name"] == check_name]
            norm_boundary = norm_ids.get(check_name, 0)
            skipped = []
            for row in norm_rows:
                if row["id"] <= norm_boundary:
                    continue
                poll_id = row.get("poll_id")
                if poll_id is None or poll_id > poll_limit:
                    continue
                recorded_at = parse_time(row.get("recorded_at"))
                if recorded_at is None or recorded_at <= cut_time:
                    skipped.append(row["id"])
            if skipped:
                errors.append(
                    f"normalisation[{check_name}] head skips historical rows: "
                    + ", ".join(str(row_id) for row_id in skipped[:20]))
    historical_collector_id = collector_id(annotation_prefix)
    if manifest.get("source_collector_id") != historical_collector_id:
        errors.append(
            "manifest.source_collector_id does not match the authenticated annotation state")
    archive_latest_poll = max((row["id"] for row in polls_all), default=max_poll_id)
    later_poll_count = sum(row["id"] > max_poll_id for row in polls_all)
    return HistoricalCut(
        poll_ids,
        norm_ids,
        annotation_id,
        annotation_head if annotation_head else GENESIS,
        historical_collector_id,
        generated_at,
        archive_latest_poll,
        later_poll_count,
        len(selected_polls),
        export_id == LEGACY_CONTROL_EVENT_EXPORT_ID,
    )


def build_projection(archive, errors, cut=None):
    all_polls = archive.rows("poll")
    all_norms = archive.rows("normalisation")
    all_annotations = archive.rows("annotation")
    polls = _rows_for_cut(all_polls, cut, cut.poll_ids if cut else {})
    norms = _rows_for_cut(all_norms, cut, cut.norm_ids if cut else {})
    annotations = _annotations_for_cut(all_annotations, cut)
    if not polls:
        errors.append("archive contains no poll rows")
        return None
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
    norm_heads = {}
    for check_name in sorted(by_check):
        poll_hashes[check_name], _ = rebuild_chain(
            by_check[check_name], poll_fields, f"poll[{check_name}]", errors)
        norm_hashes[check_name], norm_head = rebuild_chain(
            norm_by_check[check_name], normalisation_fields,
            f"normalisation[{check_name}]", errors)
        norm_heads[check_name] = norm_head
    annotation_hashes, annotation_head = rebuild_chain(
        annotations, annotation_fields, "annotation", errors)

    source_items = []
    observation_files = {}
    all_records = {}
    all_entities = {}
    all_events = []
    expected_control_ids = set()
    supported_checks = set(SOURCE_DEFINITIONS)
    generated_values = [row["polled_at"] for row in polls]

    for check_name in sorted(by_check):
        control = is_control(check_name, annotations)
        if control:
            expected_control_ids.add(slug(check_name))
        if check_name not in supported_checks and not control:
            errors.append(f"unsupported archive target is not eligible: {check_name}")
            continue
        definition = SOURCE_DEFINITIONS.get(check_name, {
            "source_type": "unknown", "category": "unclassified",
            "extractor": None, "configured_url": None,
        })
        source_id = slug(check_name)
        previous_digest = None
        previous_observation = None
        previous_records = {}
        seen_records = set()
        record_change_times = defaultdict(list)
        observations = []
        successful = []
        for sequence, poll in enumerate(by_check[check_name], 1):
            digest = poll.get("content_sha256")
            body = None
            current_records = {}
            if digest:
                try:
                    # Verify every retained digest, including a failed poll
                    # that still carries content metadata.  Only successful
                    # polls contribute structured records and source timing.
                    body = archive.blob(digest)
                    if poll["ok"]:
                        current_records = extract_structured(
                            body, definition, source_id)
                        successful.append(poll)
                except ValueError as exc:
                    errors.append(f"poll {poll['id']}: {exc}")
            norm = norm_by_poll.get(poll["id"])
            observation_id = f"{source_id}:poll:{poll['id']}"
            observation = {
                "id": observation_id,
                "target_id": source_id,
                "sequence": sequence,
                "observed_at": poll["polled_at"],
                "source": {"name": check_name, "url": poll.get("url")},
                "result": {"success": bool(poll["ok"]),
                           "http_status": poll.get("http_status"),
                           "content_length": poll.get("content_length"),
                           "etag": poll.get("etag"),
                           "last_modified": poll.get("last_modified"),
                           "error": poll.get("error")},
                "capture": {"content_sha256": digest,
                            "previous_content_sha256": previous_digest,
                            "changed": bool(poll["changed"]) if digest else None,
                            "retained": bool(body is not None),
                            "public_copy": None},
                "normalisation": None if norm is None else {
                    "id": f"{source_id}:normalisation:{norm['id']}",
                    "content_sha256": norm["content_sha256"],
                    "previous_content_sha256": previous_norm_digest(
                        norm_by_check[check_name], norm["id"]),
                    "changed": bool(norm["changed"]),
                    "transform_id": norm["transform_id"],
                    "record_hash": norm["record_hash"],
                },
                "record_ids": sorted(current_records),
                "chain": {"status": "sound",
                          "previous_entry_sha256": poll["prev_hash"],
                          "entry_sha256": poll["record_hash"],
                          "head_sha256": poll_hashes[check_name][poll["id"]]},
                "anchor": anchor_state(
                    archive, poll, poll_hashes[check_name],
                    norm_hashes[check_name].values(), annotation_hashes.values(),
                    cut),
            }
            observations.append(observation)

            legacy_control_events = bool(
                cut and cut.legacy_control_events
            )

            local_events = []
            if previous_observation and poll["ok"] and digest:
                if not control or legacy_control_events:
                    if digest != previous_digest:
                        local_events.append(event(
                            source_id, observation_id,
                            previous_observation["id"],
                            poll["polled_at"], "raw_response_changed", {
                                "mode": "raw_response",
                                "old": previous_digest,
                                "new": digest}))
                    prior_norm = previous_norm_digest(
                        norm_by_check[check_name],
                        norm["id"]) if norm else None
                    if norm and norm["changed"] and prior_norm:
                        local_events.append(event(
                            source_id, observation_id,
                            previous_observation["id"],
                            poll["polled_at"], "content_changed", {
                                "mode": "normalised_document",
                                "old": prior_norm,
                                "new": norm["content_sha256"]}))

                if not control:
                    for record_id in sorted(
                            set(previous_records) | set(current_records)):
                        old = previous_records.get(record_id)
                        new = current_records.get(record_id)
                        if old is not None and new is None:
                            local_events.append(event(
                                source_id, observation_id,
                                previous_observation["id"],
                                poll["polled_at"], "disappeared", {
                                    "mode": "structured_record",
                                    "interpretation": "record absent from a later deterministic source projection; not evidence of withdrawal",
                                    "old": old, "new": None}, record_id,
                                old.get("title")))
                            record_change_times[record_id].append(
                                poll["polled_at"])
                        elif old is not None and new is not None:
                            for event_type, comparison in compare_record(
                                    old, new):
                                local_events.append(event(
                                    source_id, observation_id,
                                    previous_observation["id"],
                                    poll["polled_at"],
                                    event_type, comparison, record_id,
                                    new.get("title")))
                                record_change_times[record_id].append(
                                    poll["polled_at"])

            if not control:
                for record_id, snapshot in sorted(current_records.items()):
                    if record_id not in seen_records:
                        local_events.append(event(
                            source_id, observation_id, None, poll["polled_at"],
                            "first_seen",
                            {"mode": "record", "new": snapshot}, record_id,
                            snapshot.get("title")))
                        seen_records.add(record_id)
                    for party in [snapshot.get("buyer"), snapshot.get("supplier")]:
                        if party:
                            all_entities[party["id"]] = {
                                **party, "record_ids": sorted(set(
                                    all_entities.get(party["id"], {}).get(
                                        "record_ids", []) + [record_id]))}
                    for party in snapshot.get("suppliers", []):
                        all_entities[party["id"]] = {
                            **party, "record_ids": sorted(set(
                                all_entities.get(party["id"], {}).get(
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
                        existing.update({key: value for key, value in snapshot.items()
                                         if key not in ("source", "dates")})
                        existing["dates"].update({
                            key: value for key, value in snapshot["dates"].items()
                            if value is not None})
                        existing["dates"]["last_observed"] = poll["polled_at"]
                        existing["source"].update(snapshot["source"])
                        existing["evidence"]["observation_ids"].append(observation_id)
                        existing["evidence"]["observation_count"] += 1

            local_events.sort(key=lambda item: (item["first_observed_at"], item["id"]))
            all_events.extend(local_events)
            if poll["ok"] and digest:
                previous_digest = digest
                previous_observation = observation
                previous_records = current_records
            elif poll["ok"] and not digest:
                previous_observation = observation

        for record_id, record in all_records.items():
            if record["source"]["target_id"] == source_id:
                record["dates"]["first_observed_changed"] = first_time(
                    record_change_times.get(record_id, []))
        observation_files[source_id] = {
            "schema_version": SCHEMA_VERSION,
            "target_id": source_id,
            "observations": observations,
        }
        source_url = definition.get("configured_url") or next(
            (row.get("url") for row in reversed(successful) if row.get("url")), None)
        coverage = defaultdict(int)
        for item in observations:
            coverage[item["anchor"]["status"]] += 1
        source_items.append({
            "id": source_id,
            "name": check_name,
            "kind": "control" if control else "source",
            "source_type": definition["source_type"],
            "category": definition["category"],
            "url": source_url,
            "configured_url": definition.get("configured_url"),
            "observation_count": len(by_check[check_name]),
            "successful_observation_count": len(successful),
            "last_observed_at": max_time([row["polled_at"] for row in successful]),
            "first_observed_at": first_time([row["polled_at"] for row in successful]),
            "last_observation_attempt_at": max_time(
                [row["polled_at"] for row in by_check[check_name]]),
            "raw_change_count": sum(bool(row["changed"])
                                     for row in by_check[check_name]),
            "document_change_count": sum(bool(row["changed"])
                                          for row in norm_by_check[check_name]),
            "recorded_change_count": sum(
                item["event_type"] != "first_seen" for item in all_events
                if item["target_id"] == source_id),
            "latest_recorded_change_at": max_time([
                item["first_observed_at"] for item in all_events
                if item["target_id"] == source_id
                and item["event_type"] != "first_seen"]),
            "chain_status": "sound",
            "chain_heads": {
                "poll": poll_hashes[check_name][by_check[check_name][-1]["id"]],
                "normalisation": (norm_heads[check_name]
                                   if norm_by_check[check_name] else GENESIS),
                "annotation": annotation_head,
            },
            "timestamp_coverage": dict(sorted(coverage.items())),
            "latest_timestamp_status": observations[-1]["anchor"]["status"],
            "record_count": sum(1 for record in all_records.values()
                                 if record["source"]["target_id"] == source_id),
        })

    all_events = sorted({item["id"]: item for item in all_events}.values(),
                        key=lambda item: (item["first_observed_at"], item["id"]))
    for record in all_records.values():
        record["evidence"]["observation_ids"] = sorted(
            record["evidence"]["observation_ids"],
            key=lambda value: int(value.rsplit(":", 1)[-1]))
    source_items.sort(key=lambda item: item["id"])
    records = sorted(all_records.values(), key=lambda item: item["id"])
    entities = sorted(all_entities.values(), key=lambda item: item["id"])
    government_sources = [item for item in source_items if item["kind"] != "control"]
    generated_at = max_time(generated_values) or "1970-01-01T00:00:00+00:00"
    coverage_counts = defaultdict(int)
    for source in government_sources:
        for status, count in source["timestamp_coverage"].items():
            coverage_counts[status] += count
    expected = {
        "sources": {"schema_version": SCHEMA_VERSION, "sources": source_items},
        "entities": {"schema_version": SCHEMA_VERSION, "entities": entities},
        "records": {"schema_version": SCHEMA_VERSION, "records": records},
        "changes": {"schema_version": SCHEMA_VERSION, "changes": all_events},
        "observations": observation_files,
        "manifest_fields": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "source_collector_id": collector_id(annotations),
            "target_count": len(source_items),
            "observation_count": sum(item["observation_count"]
                                      for item in source_items),
            "recorded_change_count": sum(
                item["event_type"] != "first_seen" for item in all_events),
            "summary": {
                "government_sources": len(government_sources),
                "control_checks": len(source_items) - len(government_sources),
                "government_observations": sum(
                    item["observation_count"] for item in government_sources),
                "recorded_changes": sum(
                    item["recorded_change_count"] for item in government_sources),
                "timestamp_coverage_counts": dict(sorted(coverage_counts.items())),
            },
            "per_target_chain_heads": [
                {"target_id": item["id"], **item["chain_heads"]}
                for item in source_items],
        },
        "control_ids": expected_control_ids,
    }
    return expected


def load_json(path, errors):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read {path}: {exc}")
        return None


def check_schema_version(label, value, errors):
    if not isinstance(value, dict):
        if value is not None:
            errors.append(f"{label} is not a JSON object")
        return
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"{label} has unsupported schema {value.get('schema_version')!r}")


def ids(items, key="id"):
    if not isinstance(items, list):
        return []
    return [item.get(key) for item in items if isinstance(item, dict)]


def report_set_difference(label, expected, actual, errors):
    expected = set(expected)
    actual = set(actual)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        errors.append(f"missing {label}: {', '.join(missing[:20])}"
                      + (" ..." if len(missing) > 20 else ""))
    if unexpected:
        errors.append(f"unexpected {label}: {', '.join(unexpected[:20])}"
                      + (" ..." if len(unexpected) > 20 else ""))


def check_unique(label, values, errors):
    duplicates = sorted({value for value in values
                         if values.count(value) > 1 and value is not None})
    if duplicates:
        errors.append(f"duplicated {label}: {', '.join(duplicates[:20])}")


def compare_exact(label, expected, actual, errors):
    if expected != actual:
        errors.append(f"{label} differs from the archive-derived projection")


def report_projection(expected):
    """Print independently derived BI coverage and activity diagnostics."""
    records = expected["records"]["records"]
    changes = expected["changes"]["changes"]
    valued = 0
    totals = defaultdict(Decimal)
    for record in records:
        value = record.get("value") or {}
        amount = value.get("amount")
        currency = value.get("currency")
        if (isinstance(amount, (int, float)) and not isinstance(amount, bool)
                and isinstance(currency, str) and currency.strip()):
            try:
                totals[currency.strip()] += Decimal(str(amount))
                valued += 1
            except (InvalidOperation, ValueError):
                pass

    generated_at = parse_time(expected["manifest_fields"]["generated_at"])
    activity = [item for item in changes
                if item.get("record_id") and item.get("event_type") != "disappeared"]
    windows = {}
    for days in (1, 7, 30):
        if generated_at is None:
            windows[days] = 0
            continue
        start = generated_at - timedelta(days=days)
        windows[days] = sum(
            1 for item in activity
            if (observed := parse_time(item.get("first_observed_at"))) is not None
            and start <= observed <= generated_at)

    print(f"procurement records: {len(records)}")
    print(f"award-value coverage: {valued}/{len(records)}")
    print("award totals: " + ", ".join(
        f"{currency} {total:.2f}" for currency, total in sorted(totals.items()))
        if totals else "award totals: none")
    print(f"record activity: 24h={windows[1]} 7d={windows[7]} 30d={windows[30]}")


def report_historical_cut(cut):
    print("verification_mode: historical")
    print(f"authenticated_cut_poll: {cut.max_poll_id}")
    print(f"archive_latest_poll: {cut.archive_latest_poll}")
    print("later_polls_ignored_as_subsequent_history: "
          f"{cut.later_poll_count}")


def _verify_export(archive_root, export_root, historical=False):
    errors = []
    export_root = Path(export_root).expanduser().resolve()

    actual = {}
    for filename in ("manifest.json", "sources.json", "entities.json",
                     "records.json", "changes.json"):
        actual[filename] = load_json(export_root / filename, errors)
    manifest = actual.get("manifest.json")
    exported_observations = {}
    observation_dir = export_root / "observations"
    if observation_dir.is_dir():
        for path in sorted(observation_dir.glob("*.json")):
            exported_observations[path.stem] = load_json(path, errors)

    try:
        archive = Archive(archive_root)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"archive open failed: {exc}", file=sys.stderr)
        return 1
    try:
        try:
            cut = (derive_historical_cut(
                        archive, manifest, errors, exported_observations)
                   if historical else None)
            expected = (build_projection(archive, errors, cut)
                        if cut is not None or not historical else None)
        except (OSError, ValueError, KeyError, TypeError, IndexError,
                sqlite3.Error) as exc:
            errors.append(f"archive projection could not be reconstructed: {exc}")
            expected = None
    finally:
        archive.close()
    if expected is None:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    actual_obs = {}
    for source_id in expected["observations"]:
        path = export_root / "observations" / f"{source_id}.json"
        actual_obs[source_id] = load_json(path, errors)
    actual_records = {}
    record_dir = export_root / "records"
    if record_dir.is_dir():
        for path in sorted(record_dir.glob("*.json")):
            actual_records[path.name] = load_json(path, errors)

    for filename, payload in actual.items():
        check_schema_version(filename, payload, errors)
    for source_id, payload in actual_obs.items():
        check_schema_version(f"observations/{source_id}.json", payload, errors)

    if any(value is None or not isinstance(value, dict)
           for value in actual.values()):
        errors.append("required public aggregate files are incomplete")
    else:
        for key in ("sources", "entities", "records", "changes"):
            expected_key = key
            compare_exact(f"{key}.json", expected[key], actual[f"{key}.json"], errors)

        expected_sources = expected["sources"]["sources"]
        actual_sources = actual["sources.json"].get("sources", [])
        report_set_difference("sources", ids(expected_sources), ids(actual_sources), errors)
        check_unique("source IDs", ids(actual_sources), errors)
        expected_obs_ids = [
            observation["id"]
            for payload in expected["observations"].values()
            for observation in payload["observations"]]
        actual_obs_ids = [
            observation["id"]
            for payload in actual_obs.values() if isinstance(payload, dict)
            for observation in payload.get("observations", [])]
        report_set_difference("observations", expected_obs_ids, actual_obs_ids, errors)
        check_unique("observation IDs", actual_obs_ids, errors)

        expected_record_ids = ids(expected["records"]["records"])
        actual_record_ids = ids(actual["records.json"].get("records", []))
        report_set_difference("records", expected_record_ids, actual_record_ids, errors)
        check_unique("record IDs", actual_record_ids, errors)
        expected_entity_ids = ids(expected["entities"]["entities"])
        actual_entity_ids = ids(actual["entities.json"].get("entities", []))
        report_set_difference("entities", expected_entity_ids, actual_entity_ids, errors)
        check_unique("entity IDs", actual_entity_ids, errors)

        expected_file_names = {f"{record_file_id(record_id)}.json"
                               for record_id in expected_record_ids}
        report_set_difference("record files", expected_file_names,
                              set(actual_records), errors)
        if set(actual_records) == expected_file_names:
            for record in expected["records"]["records"]:
                compare_exact(
                    f"records/{record_file_id(record['id'])}.json", record,
                    actual_records[f"{record_file_id(record['id'])}.json"], errors)

    files = []
    if export_root.is_dir():
        for path in sorted(export_root.rglob("*.json")):
            relative = path.relative_to(export_root).as_posix()
            if relative == "manifest.json":
                continue
            files.append({"path": relative, "sha256": sha256(path.read_bytes()),
                          "bytes": path.stat().st_size})
    expected_json_paths = {"sources.json", "entities.json", "records.json",
                           "changes.json"}
    expected_json_paths.update(
        f"observations/{source_id}.json" for source_id in expected["observations"])
    expected_json_paths.update(
        f"records/{record_file_id(record_id)}.json"
        for record_id in ids(expected["records"]["records"]))
    report_set_difference("public JSON files", expected_json_paths,
                          {item["path"] for item in files}, errors)
    if isinstance(manifest, dict):
        compare_exact("manifest.files", files, manifest.get("files"), errors)
        for item in files:
            path = export_root / item["path"]
            if not path.is_file():
                errors.append(f"manifest file is missing: {item['path']}")
        fields = expected["manifest_fields"]
        for name in ("schema_version", "generated_at", "source_collector_id",
                     "target_count", "observation_count", "recorded_change_count",
                     "summary", "per_target_chain_heads"):
            compare_exact(f"manifest.{name}", fields[name], manifest.get(name), errors)
        expected_basis = {"schema_version": SCHEMA_VERSION,
                          "generated_at": fields["generated_at"],
                          "files": files,
                          "chain_heads": [item["chain_heads"]
                                           for item in expected["sources"]["sources"]]}
        expected_export_id = "export-" + sha256(public_json(expected_basis))[:32]
        compare_exact("manifest.export_id", expected_export_id,
                      manifest.get("export_id"), errors)

    if errors:
        print("Public export verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    if historical:
        report_historical_cut(cut)
    report_projection(expected)
    print("Public export independently verified for completeness, archive "
          "correspondence, relationships, deterministic event semantics, "
          "manifest identity, and supported aggregate claims.")
    print("Scope: this does not establish source truthfulness, complete upstream "
          "polling, unsupported HTML-only semantics, human interpretation "
          "choices beyond the projection specification, or Bitcoin anchoring "
          "solely from .ots presence.")
    return 0


def verify_export(archive_root, export_root):
    """Verify a current export against the entire eligible live archive."""
    return _verify_export(archive_root, export_root, historical=False)


def verify_historical_export(archive_root, export_root):
    """Verify a snapshot against its authenticated archive prefix."""
    return _verify_export(archive_root, export_root, historical=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--historical", action="store_true",
        help="derive and verify the archive cut from the snapshot chain heads")
    parser.add_argument("archive", help="archive root containing polls.db")
    parser.add_argument("export", help="public export directory")
    args = parser.parse_args(argv)
    verifier = verify_historical_export if args.historical else verify_export
    return verifier(args.archive, args.export)


if __name__ == "__main__":
    sys.exit(main())
