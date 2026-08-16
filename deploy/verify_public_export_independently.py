#!/usr/bin/env python3
"""Read-only, standard-library verification of a public evidence export."""

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

SCHEMA_VERSION = "1.0.0"
SHA256 = re.compile(r"[0-9a-f]{64}")
RECORD_EVENTS = {"first_seen", "disappeared", "value_changed", "date_changed", "supplier_changed", "status_changed"}
SOURCE_EVENTS = {"raw_response_changed", "content_changed"}
SOURCE_RULES = {
    "contracts-finder-recent-awards": "awards",
    "find-a-tender-recent-awards": "fat-awards",
    "uk-direct-awards-no-competition": "direct",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def entity(role, raw):
    raw = raw if isinstance(raw, dict) else {}
    name, identifier = raw.get("name"), raw.get("id")
    if not name and not identifier:
        return None
    return {"id": f"entity:{role}:{identifier or name}", "name": name, "role": role,
            "source_identifier": identifier}


def party(parties, role, preferred=None):
    direct = entity(role, preferred)
    if direct:
        return direct
    return next((candidate for item in parties or []
                 if role in (item.get("roles") or [])
                 if (candidate := entity(role, item))), None)


def consistent(values):
    values = [value for value in values if value is not None]
    return values[0] if values and all(value == values[0] for value in values) else None


def snapshot(release, source_id):
    tender, parties = release.get("tender") or {}, release.get("parties") or []
    awards = [item for item in release.get("awards") or [] if isinstance(item, dict)]
    key = release.get("ocid") or release.get("id")
    if not key:
        return None
    suppliers = []
    for item in [supplier for award in awards for supplier in award.get("suppliers") or []] + [
            item for item in parties if "supplier" in (item.get("roles") or [])]:
        candidate = entity("supplier", item)
        if candidate and candidate not in suppliers:
            suppliers.append(candidate)
    value = consistent([award.get("value") for award in awards]) or (tender.get("value") if not awards else None)
    published = release.get("publishedDate") or consistent([
        document.get("datePublished") for award in awards for document in award.get("documents") or []
    ]) or release.get("date")
    periods = [award.get("contractPeriod") for award in awards] or [tender.get("contractPeriod")]
    cpv = []
    classifications = [tender.get("classification") or {}] + [classification
        for item in tender.get("items") or [] for classification in item.get("additionalClassifications") or []]
    for item in classifications:
        if str(item.get("scheme", "")).upper() == "CPV" and item.get("id"):
            candidate = {"code": str(item["id"]), "description": item.get("description")}
            if candidate not in cpv:
                cpv.append(candidate)
    cpv.sort(key=lambda item: item["code"])
    notice_urls = [document.get("url") for award in awards for document in award.get("documents") or [] if document.get("url")]
    notice_urls += [document.get("url") for document in release.get("documents") or [] if document.get("url")]
    tags = release.get("tag") or []
    return {
        "id": f"record:{source_id}:{key}",
        "type": "award" if awards or "award" in tags or "awardUpdate" in tags else "procurement",
        "title": tender.get("title"), "buyer": party(parties, "buyer", release.get("buyer")),
        "supplier": suppliers[0] if len(suppliers) == 1 else None, "suppliers": suppliers,
        "notice_type": tags[0] if tags else None,
        "dates": {"published": str(published) if published else None,
                  "award": str(consistent([award.get("date") for award in awards])) if consistent([award.get("date") for award in awards]) else None,
                  "start": str(consistent([period.get("startDate") for period in periods if period])) if consistent([period.get("startDate") for period in periods if period]) else None,
                  "end": str(consistent([period.get("endDate") for period in periods if period])) if consistent([period.get("endDate") for period in periods if period]) else None},
        "value": value if isinstance(value, dict) else None,
        "classification": {"cpv": cpv, "category": consistent([item.get("description") for item in cpv])},
        "status": consistent([award.get("status") for award in awards]) or (tender.get("status") if not awards else None),
        "source": {"notice_url": notice_urls[0] if notice_urls else None},
    }


def extract(raw, source_id):
    rule = SOURCE_RULES.get(source_id)
    if not rule:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    output = {}
    for release in payload.get("releases", []) if isinstance(payload, dict) else []:
        if not isinstance(release, dict):
            continue
        tags = release.get("tag") or []
        if rule == "fat-awards" and "award" not in tags:
            continue
        if rule == "direct" and (release.get("tender") or {}).get("procurementMethod") != "direct":
            continue
        item = snapshot(release, source_id)
        if item:
            output[item["id"]] = item
    return output


class Verification:
    def __init__(self, archive, public):
        self.archive, self.public, self.errors = archive.resolve(), public.resolve(), []

    def fail(self, message):
        self.errors.append(message)

    def run(self):
        manifest = load(self.public / "manifest.json")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            self.fail(f"unsupported manifest schema {manifest.get('schema_version')!r}")
        listed = set()
        for item in manifest.get("files", []):
            relative = item.get("path", ""); listed.add(relative)
            path = (self.public / relative).resolve()
            if self.public not in path.parents or not path.is_file():
                self.fail(f"manifest file missing or unsafe: {relative}"); continue
            data = path.read_bytes()
            if len(data) != item.get("bytes") or digest(data) != item.get("sha256"):
                self.fail(f"manifest hash/length mismatch: {relative}")
        actual = {path.relative_to(self.public).as_posix() for path in self.public.rglob("*.json") if path.name != "manifest.json"}
        if listed != actual:
            self.fail(f"manifest inventory mismatch: missing={sorted(listed-actual)} unlisted={sorted(actual-listed)}")
        sources = load(self.public / "sources.json").get("sources", [])
        records = load(self.public / "records.json").get("records", [])
        entities = load(self.public / "entities.json").get("entities", [])
        changes = load(self.public / "changes.json").get("changes", [])
        for filename in ("sources.json", "records.json", "entities.json", "changes.json"):
            if load(self.public / filename).get("schema_version") != SCHEMA_VERSION:
                self.fail(f"unsupported schema in {filename}")
        uri = "file:" + quote(str(self.archive / "polls.db"), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            polls = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM poll")}
            norms = {row["poll_id"]: dict(row) for row in conn.execute("SELECT * FROM normalisation WHERE poll_id IS NOT NULL")}
            self.verify_data(manifest, sources, records, entities, changes, polls, norms)
        return not self.errors

    def verify_data(self, manifest, sources, records, entities, changes, polls, norms):
        source_by_id = {item.get("id"): item for item in sources}
        if len(source_by_id) != len(sources): self.fail("duplicate source ID")
        observations, reconstructed, record_observations = {}, {}, defaultdict(set)
        observed_entity_links, observed_entity_facts = defaultdict(set), {}
        for source in sources:
            source_id = source["id"]
            payload = load(self.public / "observations" / f"{source_id}.json")
            if payload.get("schema_version") != SCHEMA_VERSION: self.fail(f"bad observation schema: {source_id}")
            for observation in payload.get("observations", []):
                oid = observation.get("id")
                if oid in observations: self.fail(f"duplicate observation ID: {oid}")
                observations[oid] = observation
                try: poll_id = int(oid.rsplit(":", 1)[1])
                except (AttributeError, ValueError): self.fail(f"invalid observation ID: {oid}"); continue
                poll = polls.get(poll_id)
                if not poll or poll["check_name"] != source.get("name"):
                    self.fail(f"observation does not resolve to source poll: {oid}"); continue
                expected = (poll["polled_at"], bool(poll["ok"]), poll["http_status"], poll["content_sha256"], poll["prev_hash"], poll["record_hash"])
                actual = (observation.get("observed_at"), observation.get("result", {}).get("success"), observation.get("result", {}).get("http_status"), observation.get("capture", {}).get("content_sha256"), observation.get("chain", {}).get("previous_entry_sha256"), observation.get("chain", {}).get("entry_sha256"))
                if expected != actual: self.fail(f"archive fields differ for observation: {oid}")
                norm = norms.get(poll_id); exported_norm = observation.get("normalisation")
                if bool(norm) != bool(exported_norm) or norm and (norm["content_sha256"] != exported_norm.get("content_sha256") or norm["record_hash"] != exported_norm.get("record_hash")):
                    self.fail(f"normalisation differs for observation: {oid}")
                raw = b""
                if poll.get("content_sha256"):
                    digest_value = poll["content_sha256"]
                    if not SHA256.fullmatch(digest_value): self.fail(f"invalid poll digest: {oid}"); continue
                    blob = self.archive / "blobs" / digest_value[:2] / f"{digest_value}.gz"
                    try:
                        with gzip.open(blob, "rb") as handle: raw = handle.read()
                    except (OSError, EOFError): self.fail(f"retained blob unreadable: {digest_value}"); continue
                    if digest(raw) != digest_value: self.fail(f"retained blob hash mismatch: {digest_value}")
                snapshots = extract(raw, source_id) if poll["ok"] else {}
                if sorted(snapshots) != sorted(observation.get("record_ids", [])):
                    self.fail(f"record list differs from retained source: {oid}")
                for rid, item in snapshots.items():
                    reconstructed[rid] = item; record_observations[rid].add(oid)
                    for party_item in [item.get("buyer")] + (item.get("suppliers") or []):
                        if party_item:
                            observed_entity_links[party_item["id"]].add(rid)
                            observed_entity_facts[party_item["id"]] = party_item
        record_by_id = {item.get("id"): item for item in records}
        if len(record_by_id) != len(records): self.fail("duplicate record ID")
        comparable = ("type", "title", "buyer", "supplier", "suppliers", "notice_type", "value", "classification", "status")
        for rid, record in record_by_id.items():
            if rid not in reconstructed: self.fail(f"record not reproducible from retained source: {rid}"); continue
            expected = reconstructed[rid]
            for field in comparable:
                if record.get(field) != expected.get(field): self.fail(f"record field mismatch: {rid}.{field}")
            for field in ("published", "award", "start", "end"):
                if record.get("dates", {}).get(field) != expected["dates"].get(field): self.fail(f"record date mismatch: {rid}.{field}")
            if record.get("source", {}).get("notice_url") != expected["source"].get("notice_url"): self.fail(f"source notice mismatch: {rid}")
            refs = set(record.get("evidence", {}).get("observation_ids", []))
            if refs != record_observations[rid]: self.fail(f"record evidence relationships differ: {rid}")
        entity_by_id = {item.get("id"): item for item in entities}
        if set(entity_by_id) != set(observed_entity_links): self.fail("entity ID set does not reproduce observed record relationships")
        for eid, linked in observed_entity_links.items():
            item = entity_by_id.get(eid, {})
            if set(item.get("record_ids", [])) != linked: self.fail(f"entity relationship mismatch: {eid}")
            for field in ("name", "role", "source_identifier"):
                if item.get(field) != observed_entity_facts[eid].get(field): self.fail(f"entity field mismatch: {eid}.{field}")
        allowed = RECORD_EVENTS | SOURCE_EVENTS
        for change in changes:
            if change.get("event_type") not in allowed: self.fail(f"invalid event type: {change.get('id')}")
            if change.get("observation_id") not in observations: self.fail(f"event observation missing: {change.get('id')}")
            rid = change.get("record_id")
            if rid and rid not in record_by_id: self.fail(f"event record missing: {change.get('id')}")
            source = source_by_id.get(change.get("target_id"))
            if not source: self.fail(f"event source missing: {change.get('id')}")
            if rid and source and source.get("kind") == "control": self.fail(f"control event masquerades as BI activity: {change.get('id')}")
            if change.get("event_type") in RECORD_EVENTS and not rid: self.fail(f"record event has no record: {change.get('id')}")
            if change.get("event_type") in SOURCE_EVENTS and rid: self.fail(f"source event has a record: {change.get('id')}")
        if manifest.get("target_count") != len(sources): self.fail("manifest target_count mismatch")
        if manifest.get("observation_count") != len(observations): self.fail("manifest observation_count mismatch")
        if manifest.get("recorded_change_count") != sum(item.get("event_type") != "first_seen" for item in changes): self.fail("manifest recorded_change_count mismatch")
        self.report(source_by_id, records, entities, changes, manifest.get("generated_at"))

    def report(self, sources, records, entities, changes, generated_at):
        business = [record for record in records if sources.get(record.get("source", {}).get("target_id"), {}).get("kind") != "control"]
        buyers = {record["buyer"]["id"] for record in business if record.get("buyer")}
        suppliers = {item["id"] for record in business for item in record.get("suppliers") or []}
        totals, valued = defaultdict(float), 0
        for record in business:
            value = record.get("value") or {}
            if isinstance(value.get("amount"), (int, float)) and value.get("currency"):
                totals[value["currency"]] += value["amount"]; valued += 1
        activity = [item for item in changes if item.get("record_id") and item.get("event_type") != "disappeared" and sources.get(item.get("target_id"), {}).get("kind") != "control"]
        try: end = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError): end = datetime.min.replace(tzinfo=timezone.utc); self.fail("invalid generated_at")
        windows = {days: sum(end - timedelta(days=days) <= datetime.fromisoformat(item["first_observed_at"].replace("Z", "+00:00")) <= end for item in activity) for days in (1, 7, 30)}
        print(f"procurement records: {len(business)}")
        print(f"buyers: {len(buyers)}; suppliers: {len(suppliers)}")
        print(f"award-value coverage: {valued}/{len(business)}")
        print("award totals: " + ", ".join(f"{currency} {total:.2f}" for currency, total in sorted(totals.items())))
        print(f"record activity: 24h={windows[1]} 7d={windows[7]} 30d={windows[30]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_root", type=Path)
    parser.add_argument("public_export", type=Path)
    args = parser.parse_args(argv)
    verifier = Verification(args.archive_root, args.public_export)
    try: ok = verifier.run()
    except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as error:
        verifier.fail(f"verification could not complete: {error}"); ok = False
    if verifier.errors:
        print("\nFAILED", file=sys.stderr)
        for item in verifier.errors: print(f"- {item}", file=sys.stderr)
        return 1
    print("public export independently verified")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
