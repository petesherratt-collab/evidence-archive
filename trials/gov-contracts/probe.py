#!/usr/bin/env python3
"""Find a selector that produces a stable hash.

Fetches a URL twice and diffs the results. Anything that differs between two
requests seconds apart cannot be a real change at the source, so it is churn
you must select away: rotating banners, session tokens, CSRF fields, ad slots,
"generated at" stamps, A/B variant markers.

This is the whole normalisation problem in one tool. Getting it wrong is the
difference between an archive that grows at 60 KB per target per year and one
that grows at 35 GB, and between two useful alerts a week and fifty useless
ones a day.

Usage:
    ./probe.py URL                       # is the whole page stable?
    ./probe.py URL --selector "main"     # is this region stable?
    ./probe.py URL --suggest             # list candidate containers by size
    ./probe.py URL --selector "main" --text   # compare extracted text only
"""
import argparse
import difflib
import hashlib
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("pip install requests lxml")

try:
    from lxml import html as lxml_html
except ImportError:
    sys.exit("pip install requests lxml")


USER_AGENT = "policy-archive-probe/0.1 (+https://example.org/about-this-crawler)"
DEFAULT_GAP = 5


def fetch(url, timeout=30):
    """Fetch a URL with caching defeated, returning (status, text)."""
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    return response.status_code, response.text


def select(content, selector, as_text):
    """Apply a CSS selector, optionally reducing to visible text."""
    tree = lxml_html.fromstring(content)
    nodes = tree.cssselect(selector) if selector else [tree]
    if not nodes:
        return None
    if as_text:
        return "\n".join(
            " ".join(node.text_content().split()) for node in nodes
        )
    return "\n".join(
        lxml_html.tostring(node, encoding="unicode") for node in nodes
    )


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def suggest(content, limit=15):
    """List candidate containers, largest text volume first."""
    tree = lxml_html.fromstring(content)
    seen = []
    for node in tree.iter():
        if not isinstance(node.tag, str):
            continue
        text = " ".join(node.text_content().split())
        if len(text) < 200:
            continue
        parts = [node.tag]
        node_id = node.get("id")
        classes = (node.get("class") or "").split()
        if node_id:
            parts.append(f"#{node_id}")
        if classes:
            parts.append("." + ".".join(classes[:3]))
        seen.append((len(text), "".join(parts)))

    seen.sort(reverse=True)
    print(f"\nCandidate containers (>=200 chars of text), largest first:\n")
    for size, path in seen[:limit]:
        print(f"  {size:>8,}  {path}")
    print("\nPrefer the smallest container that still holds the content you "
          "care about.\nThe bigger the selection, the more churn you inherit.")


def report_diff(first, second, label):
    """Print a unified diff of two fetches, or confirm stability."""
    if first == second:
        print(f"\n  STABLE — {label} is byte-identical across two fetches.")
        print(f"  sha256: {digest(first)}")
        return True

    print(f"\n  UNSTABLE — {label} differs between two fetches seconds apart.")
    print(f"  sha256 #1: {digest(first)}")
    print(f"  sha256 #2: {digest(second)}")

    diff = list(difflib.unified_diff(
        first.splitlines(), second.splitlines(),
        fromfile="fetch-1", tofile="fetch-2", lineterm="", n=1,
    ))
    shown = diff[:60]
    print(f"\n  Churn ({len(diff)} diff lines, showing {len(shown)}):\n")
    for line in shown:
        print("    " + line)
    print("\n  Each difference above is something to select away or strip.")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--selector", help="CSS selector to test")
    parser.add_argument("--text", action="store_true",
                        help="compare extracted text rather than markup")
    parser.add_argument("--suggest", action="store_true",
                        help="list candidate containers and exit")
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP,
                        help=f"seconds between fetches (default {DEFAULT_GAP})")
    args = parser.parse_args()

    print(f"Fetching {args.url} ...")
    status, first_raw = fetch(args.url)
    print(f"  HTTP {status}, {len(first_raw):,} bytes")
    if status != 200:
        print("  Non-200: fix this before tuning selectors.")
        return 1

    if args.suggest:
        suggest(first_raw)
        return 0

    print(f"  Waiting {args.gap}s before second fetch ...")
    time.sleep(args.gap)
    _, second_raw = fetch(args.url)

    label = f"selector {args.selector!r}" if args.selector else "whole page"
    first = select(first_raw, args.selector, args.text)
    second = select(second_raw, args.selector, args.text)

    if first is None or second is None:
        print(f"\n  Selector {args.selector!r} matched nothing. "
              f"Try --suggest to see what is there.")
        return 1

    stable = report_diff(first, second, label)

    if not stable and not args.text:
        print("\n  Next: try --text to ignore markup churn, then narrow the "
              "selector.")
    elif not stable:
        print("\n  Text still unstable: the churn is in the content itself. "
              "Narrow the selector further, or strip the offending element.")

    return 0 if stable else 2


if __name__ == "__main__":
    sys.exit(main())
