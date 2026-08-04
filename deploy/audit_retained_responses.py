#!/usr/bin/env python3
"""Per-poll audit: is each retained response bound to its anchored digest?

One question, asked of every poll row that recorded content:

    the blob located by this row's `content_sha256` — the digest the record
    hash commits to, and that an anchor therefore commits to — is present, and
    decompresses to bytes hashing to that same digest.

`raw_ref` is deliberately not used to find anything. It is not a hashed field,
so a blob reached through it is bound to nothing; resolving through it is what
let a forged response be substituted on an anchored poll while `verify`, `fsck`
and `verify_independently.py` all reported the archive sound. `raw_ref` is
reported here only so a disagreement with `content_sha256` is visible.

A row that fails is a retained response genuinely lost or altered. A row that
passes needs no further attestation: the digest it was checked against is
already inside whatever proof covers that poll.

    python3 audit_retained_responses.py [archive_root] [--csv out.csv]
"""
import argparse
import csv
import gzip
import hashlib
import os
import sqlite3
import sys


def audit_row(root, row):
    """Return (status, detail) for one poll row that recorded content."""
    digest = row["content_sha256"]
    if row["raw_ref"] != digest:
        return "UNBOUND", (f"raw_ref {str(row['raw_ref'])[:16]} is not the "
                           f"attested {str(digest)[:16]}")
    path = os.path.join(root, "blobs", digest[:2], digest + ".gz")
    if not os.path.exists(path):
        return "MISSING", "no blob at the attested digest"
    try:
        with gzip.open(path, "rb") as handle:
            data = handle.read()
    except (OSError, EOFError) as exc:
        return "UNREADABLE", str(exc)
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest:
        return "MISMATCH", f"blob hashes to {actual[:16]}"
    return "PASS", f"{len(data)} bytes"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="archive")
    parser.add_argument("--csv", help="write the per-poll table here")
    args = parser.parse_args(argv)

    db = sqlite3.connect(os.path.join(args.root, "polls.db"))
    db.row_factory = sqlite3.Row

    # Anchored-ness is reported per row so a failure can be read for what it
    # costs: an unanchored poll losing its response is a loss, an anchored one
    # losing it is a loss of something that was provably held at a known time.
    anchored_upto = {}
    for row in db.execute("SELECT check_name, MAX(last_poll_id) AS upto"
                          " FROM anchor GROUP BY check_name"):
        anchored_upto[row["check_name"]] = row["upto"] or 0

    rows = db.execute(
        "SELECT id, check_name, polled_at, ok, content_sha256, raw_ref"
        " FROM poll ORDER BY id").fetchall()

    results, counts = [], {}
    for row in rows:
        if row["content_sha256"] is None:
            # A failed poll retained nothing; there is no response to bind.
            status, detail = "NO CONTENT", (
                "poll recorded no response" if not row["ok"]
                else "successful poll with no retained content")
        else:
            status, detail = audit_row(args.root, row)
        anchored = row["id"] <= anchored_upto.get(row["check_name"], 0)
        counts[status] = counts.get(status, 0) + 1
        results.append({
            "poll_id": row["id"],
            "check": row["check_name"],
            "polled_at": row["polled_at"],
            "content_sha256": row["content_sha256"] or "",
            "anchored": "yes" if anchored else "no",
            "status": status,
            "detail": detail,
        })

    width = max(len(r["check"]) for r in results)
    for r in results:
        print(f"{r['poll_id']:>4}  {r['polled_at']}  {r['check']:<{width}}  "
              f"{'A' if r['anchored'] == 'yes' else '-'}  "
              f"{r['status']:<10}  {r['detail']}")

    print("\n" + "-" * 60)
    for status in sorted(counts):
        print(f"{counts[status]:>5}  {status}")
    failures = sum(n for s, n in counts.items()
                   if s not in ("PASS", "NO CONTENT"))
    checked = counts.get("PASS", 0) + failures
    print(f"\n{counts.get('PASS', 0)} of {checked} retained response(s) are "
          f"present and hash to the digest their poll attests to.")
    anchored_pass = sum(1 for r in results
                        if r["status"] == "PASS" and r["anchored"] == "yes")
    print(f"{anchored_pass} of those are already covered by an existing "
          f"proof, and need no further attestation.")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)
        print(f"per-poll table written to {args.csv}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
