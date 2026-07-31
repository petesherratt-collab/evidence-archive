# kibitzr-archive

Poll logging and raw-response retention for [kibitzr](https://github.com/kibitzr/kibitzr).

A kibitzr plugin, not a fork. It installs through the `kibitzr.fetcher`
entry point and applies to any kibitzr tree, hardened or stock.

## What it adds, and why

Kibitzr's storage is change-only. `PageHistory.commit()` runs `git commit`
and treats the resulting exit-1 as "nothing changed", so an unchanged poll
writes nothing at all. It also stores only the *transformed* text — the
fetched response is discarded before anything can retain it.

That is fine for notification. It is not enough for an archive intended as
evidence, for two reasons:

1. **Silence is ambiguous.** With no record of unchanged polls, a gap in
   the history cannot distinguish "we checked every six hours for three
   years and it never moved" from "we were not watching". The first is a
   finding; the second is an absence of data. An evidential claim needs
   them to be different.

2. **Extraction is unverifiable.** If only the post-transform text is
   kept, nobody — including you — can check that the selectors extracted
   faithfully. A third party has to take the extraction on trust, which is
   precisely the dependency the archive exists to remove.

This plugin fixes both, and puts the hash where anchoring attaches.

## Install

```bash
pip install -e tools/kibitzr-archive
```

## Use

Opt in per check. Checks without `archive` are untouched and continue to
use kibitzr's built-in fetchers.

```yaml
checks:
  - name: Example Usage Policy
    url: https://example.com/policy
    archive: true                 # or: archive: {root: ./archive}
    transform:
      - css: main
      - changes
    notify:
      - slack
```

The plugin wraps the fetch, records the observation, and passes content
through unmodified. The `changes` transform, its git history and all
notifiers behave exactly as before.

## What gets stored

`archive/polls.db` — one row per poll, always:

| column | notes |
|---|---|
| `check_name`, `url`, `polled_at` | UTC, second precision |
| `ok`, `http_status`, `error` | a failed fetch is logged, and is not a change |
| `content_sha256`, `content_length` | hash of the response as fetched |
| `etag`, `last_modified` | server-reported, where given |
| `changed` | against the last poll that observed content |
| `raw_ref` | digest of the retained response |
| `prev_hash`, `record_hash` | chain links |

`archive/blobs/` — gzipped, content-addressed, write-once. Deduplicated by
digest, so a document flapping between two states costs storage once.

## The hash chain

Each poll's `record_hash` covers its identifying fields plus the previous
poll's `record_hash`, serialised canonically so anyone holding a row can
recompute it. Editing or deleting a logged row breaks the chain from that
point on:

```python
store.verify_chain("Example Usage Policy")   # (True, None) if intact
store.head("Example Usage Policy")           # current chain head
```

`head()` is the anchoring seam. Submitting it for external timestamping
anchors every poll recorded up to that point — one anchor per check per
batch, rather than one per observation.

This matters because **git history is not evidence**. Kibitzr initialises
each page repo with a hardcoded `user.email` and `user.name`, commits are
unsigned, and commit timestamps come from the local clock — all rewritable
with `git rebase` or `--date`. The chain here is likewise only as good as
its anchor: it makes tampering *detectable by anyone holding an earlier
head*, and nothing more, until that head is committed to an external
timestamp. The anchor is the evidence; this is the thing the anchor points
at.

## Deliberate limits

- **Normalisation is not versioned.** If you change a check's selectors,
  the extracted text changes for reasons that have nothing to do with the
  publisher, and nothing here records which rules produced which capture.
  Retaining the raw response means the extraction can be re-derived after
  the fact, which mitigates but does not solve it. Open Terms Archive
  solves this properly with dated declaration and filter histories.
- **Archiving never fails a check.** A storage error is logged and
  swallowed; monitoring keeps running. Watch the logs rather than assuming
  silence means success.
- **Growth rate is your normalisation alarm.** `store.stats()` reports
  polls, changes, blobs and bytes. A target reporting far more changes
  than its document plausibly has is a target with broken selectors —
  catch it there, not in your inbox.

## Tests

```bash
pip install -e ".[test]"
pytest tests/
```

The suite covers the store standalone, and end-to-end through kibitzr's
own fetcher selection against a live local server.
