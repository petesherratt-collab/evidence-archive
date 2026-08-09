#!/usr/bin/env python3
"""Fail-closed collector/control health check with distinct fault domains."""

import argparse
import re
import sys
import urllib.request
from datetime import datetime, timezone

from kibitzr_archive.store import ArchiveStore

STAMP = re.compile(rb'datetime="([^"]+)"')


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def body_time(body: bytes) -> datetime:
    match = STAMP.search(body)
    if not match:
        raise ValueError("control response has no machine-readable generation time")
    return instant(match.group(1).decode("ascii"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--max-publisher-age", type=int, required=True)
    parser.add_argument("--max-poll-age", type=int, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)

    try:
        request = urllib.request.Request(args.url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=20) as response:
            live_generated = body_time(response.read(1_000_001))
    except Exception as exc:
        print(f"CONTROL_PUBLISHER_UNREACHABLE: {exc}", file=sys.stderr)
        return 1

    publisher_age = (now - live_generated).total_seconds()
    if publisher_age < -300 or publisher_age > args.max_publisher_age:
        print(f"CONTROL_PUBLISHER_STALE: generation age {publisher_age:.0f}s", file=sys.stderr)
        return 1

    store = ArchiveStore(args.archive)
    observations = store.observations(args.control)
    if not observations:
        print("COLLECTOR_CONTROL_MISSING: no retained control response", file=sys.stderr)
        return 1
    latest = observations[-1]
    try:
        archived_generated = body_time(store.get_blob(latest["content_sha256"]))
        polled_at = instant(latest["polled_at"])
    except Exception as exc:
        print(f"COLLECTOR_CONTROL_UNREADABLE: {exc}", file=sys.stderr)
        return 1

    with store._connect() as connection:  # deployment cross-check of linked transform
        transformed = connection.execute(
            "SELECT 1 FROM normalisation WHERE poll_id = ?", (latest["id"],)
        ).fetchone()
    if not transformed:
        print("COLLECTOR_TRANSFORM_MISSING: latest control poll has no normalisation", file=sys.stderr)
        return 1

    poll_age = (now - polled_at).total_seconds()
    publisher_gap = (live_generated - archived_generated).total_seconds()
    if poll_age < -300 or poll_age > args.max_poll_age:
        print(f"COLLECTOR_POLL_STALE: last control poll age {poll_age:.0f}s", file=sys.stderr)
        return 1
    if publisher_gap > args.max_poll_age:
        print(f"COLLECTOR_CONTROL_BEHIND: publisher gap {publisher_gap:.0f}s", file=sys.stderr)
        return 1

    print(
        "HEALTH OK: publisher_age={:.0f}s poll_age={:.0f}s publisher_gap={:.0f}s".format(
            publisher_age, poll_age, publisher_gap
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
