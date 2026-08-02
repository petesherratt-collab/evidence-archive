#!/usr/bin/env python3
"""Verify the archive using only VERIFYING.md and the standard library.

Deliberately shares no code with `kibitzr-archive`: it imports nothing from the
plugin and reimplements the specification from the document. That is the point.
A verifier built from the archiver's own functions proves the archiver is
self-consistent, which is not the question anyone is asking.

If this and the plugin ever disagree, the specification is wrong or one of the
implementations is — and that is a finding worth having.

    python3 verify_independently.py [archive_root]
"""
import glob
import gzip
import hashlib
import json
import os
import sqlite3
import sys


GENESIS = "0" * 64


def canonical(payload):
    """Section 3: sorted keys, no insignificant whitespace, non-ASCII escaped."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()


def record_hash(fields, prev_hash, version):
    return hashlib.sha256(
        canonical(dict(fields, v=version, prev=prev_hash))).hexdigest()


def check_chain(rows, build_fields, version, label):
    """Walk one chain, returning a list of problems."""
    problems = []
    prev = GENESIS
    for row in rows:
        if row["prev_hash"] != prev:
            problems.append(f"{label}: row {row['id']} does not follow its "
                            f"predecessor")
            return problems
        expected = record_hash(build_fields(row), prev, version)
        if expected != row["record_hash"]:
            problems.append(f"{label}: row {row['id']} hash mismatch "
                            f"(recomputed {expected[:16]}..., stored "
                            f"{row['record_hash'][:16]}...)")
            return problems
        prev = row["record_hash"]
    return problems


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
    # Omitted entirely when NULL -- this is what keeps rows written before the
    # column existed verifiable.
    if row["fetch_id"] is not None:
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
        "detail": row["detail"],          # the stored string, not re-serialised
    }


def combined_head(poll_head, norm_head, ann_head):
    return hashlib.sha256(canonical({
        "poll": poll_head, "norm": norm_head, "ann": ann_head, "v": 2,
    })).hexdigest()


def main(root="archive"):
    db = sqlite3.connect(os.path.join(root, "polls.db"))
    db.row_factory = sqlite3.Row
    problems = []

    checks = [r[0] for r in db.execute(
        "SELECT DISTINCT check_name FROM poll ORDER BY check_name")]

    heads = {}
    for check in checks:
        polls = db.execute(
            "SELECT * FROM poll WHERE check_name = ? ORDER BY id",
            (check,)).fetchall()
        problems += check_chain(polls, poll_fields, 1, f"poll[{check}]")

        norms = db.execute(
            "SELECT * FROM normalisation WHERE check_name = ? ORDER BY id",
            (check,)).fetchall()
        problems += check_chain(norms, normalisation_fields, 1,
                                f"norm[{check}]")

        heads[check] = (polls[-1]["record_hash"] if polls else None,
                        norms[-1]["record_hash"] if norms else GENESIS)

    annotations = db.execute("SELECT * FROM annotation ORDER BY id").fetchall()
    problems += check_chain(annotations, annotation_fields, 1, "annotation")
    ann_head = (annotations[-1]["record_hash"] if annotations else GENESIS)

    print(f"{len(checks)} check(s), "
          f"{db.execute('SELECT COUNT(*) FROM poll').fetchone()[0]} polls, "
          f"{len(annotations)} annotations")

    # -- retained responses re-hash to what the log claims -------------------
    blobs = mismatched = 0
    for row in db.execute(
            "SELECT DISTINCT raw_ref FROM poll WHERE raw_ref IS NOT NULL"):
        digest = row["raw_ref"]
        path = os.path.join(root, "blobs", digest[:2], digest + ".gz")
        if not os.path.exists(path):
            problems.append(f"blob missing for {digest[:16]}...")
            continue
        with gzip.open(path, "rb") as handle:
            actual = hashlib.sha256(handle.read()).hexdigest()
        blobs += 1
        if actual != digest:
            mismatched += 1
            problems.append(f"blob {digest[:16]}... does not hash to its name")
    print(f"{blobs} retained response(s) re-hashed, {mismatched} mismatched")

    # -- anchors -------------------------------------------------------------
    anchored = 0
    for manifest_path in sorted(glob.glob(os.path.join(root, "anchors",
                                                       "*.json"))):
        with open(manifest_path, "rb") as handle:
            raw = handle.read()
        manifest = json.loads(raw)
        proof = manifest_path + ".ots"
        if not os.path.exists(proof):
            problems.append(f"{os.path.basename(manifest_path)}: no .ots proof")
            continue
        for entry in manifest["checks"]:
            recorded = heads.get(entry["check"])
            if recorded is None:
                problems.append(f"anchor names unknown check {entry['check']!r}")
                continue
            poll_head, norm_head = recorded
            expected = combined_head(entry["poll_head"], entry["norm_head"],
                                     entry["annotation_head"])
            if expected != entry["combined_head"]:
                problems.append(
                    f"anchor {os.path.basename(manifest_path)}: combined_head "
                    f"for {entry['check']!r} does not follow from its parts")
        anchored += 1
        print(f"manifest {os.path.basename(manifest_path)}: "
              f"sha256 {hashlib.sha256(raw).hexdigest()[:16]}... "
              f"({len(manifest['checks'])} checks)")
    print(f"{anchored} manifest(s) checked "
          f"(run `ots verify` on each .ots for the time proof)")

    # Current heads are reported so they can be compared against a future
    # anchor, and against anyone else's copy of this archive.
    print("\ncurrent combined heads:")
    for check in checks:
        poll_head, norm_head = heads[check]
        print(f"  {combined_head(poll_head, norm_head, ann_head)}  {check}")

    if problems:
        print(f"\n{len(problems)} PROBLEM(S):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nAll chains, blobs and anchor manifests verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
