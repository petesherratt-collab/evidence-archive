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
    """Walk one chain, returning (problems, [(row_id, recomputed_hash), ...]).

    The recomputed hashes are returned, not just a verdict, because that is
    what an anchor has to be checked against. A row's stored ``record_hash`` is
    a claim by whoever last wrote it; anyone able to edit a row can edit its
    stored hash, and the ``anchor`` table beside it, in the same transaction.
    Only a hash recomputed from the row's own fields is independent of them.
    """
    problems, hashes = [], []
    prev = GENESIS
    for row in rows:
        if row["prev_hash"] != prev:
            problems.append(f"{label}: row {row['id']} does not follow its "
                            f"predecessor")
            return problems, hashes
        expected = record_hash(build_fields(row), prev, version)
        if expected != row["record_hash"]:
            problems.append(f"{label}: row {row['id']} hash mismatch "
                            f"(recomputed {expected[:16]}..., stored "
                            f"{row['record_hash'][:16]}...)")
            return problems, hashes
        hashes.append((row["id"], expected))
        prev = expected
    return problems, hashes


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
    poll_hashes = {}     # check -> {poll_id: recomputed hash}
    norm_hashes = {}     # check -> {recomputed hashes}
    for check in checks:
        polls = db.execute(
            "SELECT * FROM poll WHERE check_name = ? ORDER BY id",
            (check,)).fetchall()
        found, traced = check_chain(polls, poll_fields, 1, f"poll[{check}]")
        problems += found
        poll_hashes[check] = dict(traced)

        norms = db.execute(
            "SELECT * FROM normalisation WHERE check_name = ? ORDER BY id",
            (check,)).fetchall()
        found, traced_norm = check_chain(norms, normalisation_fields, 1,
                                         f"norm[{check}]")
        problems += found
        norm_hashes[check] = {h for _id, h in traced_norm}

        # Recomputed, not read off the last row.
        heads[check] = (traced[-1][1] if traced else None,
                        traced_norm[-1][1] if traced_norm else GENESIS)

    annotations = db.execute("SELECT * FROM annotation ORDER BY id").fetchall()
    found, traced_ann = check_chain(annotations, annotation_fields, 1,
                                    "annotation")
    problems += found
    ann_hashes = {h for _id, h in traced_ann}
    ann_head = traced_ann[-1][1] if traced_ann else GENESIS

    print(f"{len(checks)} check(s), "
          f"{db.execute('SELECT COUNT(*) FROM poll').fetchone()[0]} polls, "
          f"{len(annotations)} annotations")

    # -- retained responses re-hash to the digest the CHAIN commits to -------
    #
    # Resolved by `content_sha256`, never by `raw_ref`. Section 4 of
    # VERIFYING.md lists `raw_ref` among the columns that are *not* hashed, so
    # it carries no more authority than any other unattested field: an archive
    # holding a forged response under its own true digest, with `raw_ref`
    # repointed at it and the original deleted, satisfies every check that
    # resolves through `raw_ref` while contradicting the anchored row.
    # `content_sha256` is hashed into the poll chain, so reaching the blob
    # through it is what ties the bytes on disk to the proof.
    for row in db.execute(
            "SELECT id, raw_ref, content_sha256 FROM poll"
            " WHERE raw_ref IS NOT content_sha256"):
        problems.append(
            f"poll {row['id']}: raw_ref {str(row['raw_ref'])[:16]}... is not "
            f"the attested content_sha256 {str(row['content_sha256'])[:16]}...")

    blobs = mismatched = 0
    for row in db.execute("SELECT DISTINCT content_sha256 FROM poll"
                          " WHERE content_sha256 IS NOT NULL"):
        digest = row["content_sha256"]
        path = os.path.join(root, "blobs", digest[:2], digest + ".gz")
        if not os.path.exists(path):
            problems.append(f"blob missing for {digest[:16]}...")
            continue
        with gzip.open(path, "rb") as handle:
            actual = hashlib.sha256(handle.read()).hexdigest()
        blobs += 1
        if actual != digest:
            mismatched += 1
            problems.append(
                f"blob {digest[:16]}... does not hash to the content_sha256 "
                f"its poll row attests to")
    print(f"{blobs} retained response(s) re-hashed against the chain, "
          f"{mismatched} mismatched")

    # -- anchors -------------------------------------------------------------
    #
    # The trust chain runs one way only:
    #
    #   .ots proof -> manifest bytes -> heads and last_poll_id in the manifest
    #              -> chains recomputed from polls.db
    #
    # Every link is checked against the one before it, and nothing consults the
    # `anchor` table. That table is an index: it lives in the same database as
    # the rows an anchor exists to pin down, so it is writable by exactly the
    # party an anchor is supposed to constrain.
    #
    # This section previously checked each manifest's internal arithmetic —
    # that `combined_head` followed from the three heads printed beside it —
    # and never compared any of them to the database. A manifest is
    # self-consistent by construction, so that check could not fail on a
    # forged archive: it fetched the recomputed heads into `poll_head,
    # norm_head` and then never used them.
    anchored = 0
    for manifest_path in sorted(glob.glob(os.path.join(root, "anchors",
                                                       "*.json"))):
        label = os.path.basename(manifest_path)
        with open(manifest_path, "rb") as handle:
            raw = handle.read()
        manifest = json.loads(raw)
        proof = manifest_path + ".ots"
        if not os.path.exists(proof):
            problems.append(f"{label}: no .ots proof")
            continue

        for entry in manifest["checks"]:
            check = entry["check"]
            if check not in poll_hashes or not poll_hashes[check]:
                problems.append(
                    f"{label}: anchors {check!r}, which this log has no "
                    f"verifiable polls for")
                continue

            # 1. The poll head, at the exact row the manifest names.
            last_id = entry.get("last_poll_id")
            if last_id is None:
                problems.append(f"{label}: {check!r} entry names no "
                                f"last_poll_id")
            elif last_id not in poll_hashes[check]:
                problems.append(
                    f"{label}: {check!r} is anchored at poll id {last_id}, "
                    f"which this log does not contain as a verified row")
            elif poll_hashes[check][last_id] != entry["poll_head"]:
                problems.append(
                    f"{label}: {check!r} poll id {last_id} recomputes to "
                    f"{poll_hashes[check][last_id][:16]}..., not the anchored "
                    f"{entry['poll_head'][:16]}...")

            # 2. The other two heads are not tied to a row id, so the
            #    requirement is that each occurs in its rebuilt chain. GENESIS
            #    means the chain was empty when stamped.
            if (entry["norm_head"] != GENESIS
                    and entry["norm_head"] not in norm_hashes[check]):
                problems.append(
                    f"{label}: {check!r} is anchored at normalisation head "
                    f"{entry['norm_head'][:16]}..., which does not occur in "
                    f"the chain rebuilt from this log")
            if (entry["annotation_head"] != GENESIS
                    and entry["annotation_head"] not in ann_hashes):
                problems.append(
                    f"{label}: anchored at annotation head "
                    f"{entry['annotation_head'][:16]}..., which does not occur "
                    f"in the annotation chain rebuilt from this log")

            # 3. Only now is the manifest's own arithmetic worth checking:
            #    its parts have each been located in a rebuilt chain.
            expected = combined_head(entry["poll_head"], entry["norm_head"],
                                     entry["annotation_head"])
            if expected != entry["combined_head"]:
                problems.append(
                    f"{label}: combined_head for {check!r} does not follow "
                    f"from its parts")

        anchored += 1
        print(f"manifest {label}: sha256 "
              f"{hashlib.sha256(raw).hexdigest()[:16]}... "
              f"({len(manifest['checks'])} checks) bound to the log")

    # 4. The last link, and the only one establishing *time*. Everything above
    #    proves the manifest describes this log; it says nothing about when the
    #    log existed. That comes from the proof over these exact manifest
    #    bytes, and verifying it needs the OpenTimestamps client and a Bitcoin
    #    node. Reported honestly rather than implied: an unverified proof is
    #    not a failure of this script, but it is also not a proof yet.
    print(f"\n{anchored} manifest(s) bound to the log. The time proof is "
          f"separate:\n  for each anchors/*.json, run `ots verify <file>.ots`\n"
          f"  a proof still on calendar attestations needs `ots upgrade` first")

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
