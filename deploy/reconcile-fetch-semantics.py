#!/usr/bin/env python3
"""Append, never rewrite, corrections for the regime-3 origin overclaim."""

import argparse

from kibitzr_archive.store import ArchiveStore

OLD = "reaches the origin"
CORRECT = (
    "Regime 3 removed Kibitzr's local response cache and performs a network "
    "fetch for each poll. It does not prove that an origin server, rather than "
    "a CDN or reverse proxy, served the representation."
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = ArchiveStore(args.archive)
    old = [
        row for row in store.annotations(kind="fetch_regime")
        if row["detail"].get("semantics") == 3
        and OLD in row["detail"].get("note", "")
    ]
    prior = [
        row for row in store.annotations(kind="correction")
        if row["detail"].get("correction") == "fetch_semantics_3_origin_wording"
    ]
    if prior:
        print("Correction already recorded; no action.")
        return 0
    if not old:
        print("No old regime-3 wording found; no action.")
        return 0
    ids = [row["id"] for row in old]
    print(f"Found {len(ids)} annotation(s) requiring correction: {ids}")
    if not args.apply:
        print("Dry run only. Re-run with --apply after a verified off-machine backup.")
        return 2
    digest = store.record_annotation(
        "correction",
        {
            "correction": "fetch_semantics_3_origin_wording",
            "corrects_annotation_ids": ids,
            "replacement_note": CORRECT,
            "reason": "Local cache removal cannot exclude an intermediary cache.",
        },
    )
    print(f"Recorded append-only correction: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
