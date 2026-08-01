# kibitzr-archive

Poll logging and raw-response retention for [kibitzr](https://github.com/kibitzr/kibitzr).

A kibitzr plugin, not a fork. It installs through the `kibitzr.fetcher`,
`kibitzr.cli` and `kibitzr.before_start` entry points, and applies to any
kibitzr tree, hardened or stock.

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

### Identifying the collector

kibitzr sends `User-agent: Kibitzr/<version>`, which names the tool but not
whoever is running it. An archive that expects to be believed should say who
collected it and how to make contact, so every archived fetch instead sends:

```
EvidenceArchive/0.1 (+https://github.com/petesherratt-collab/evidence-archive)
```

Override it per check, or opt out by name:

```yaml
    user_agent: "YourArchive/1.0 (+mailto:you@example.org)"
    user_agent: false             # fall back to kibitzr's own header
```

Set this before the first poll. It is not a property that can be added to
records afterwards: fetches made under an anonymous agent were made under an
anonymous agent, whatever the config says later.

Only the requests path is covered. A check routed through Firefox sends the
browser's UA, and this setting does not reach it.

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

`archive/polls.db`, table `normalisation` — one row per poll whose transform
chain produced content, holding the hash of the document *after* selection:

| column | notes |
|---|---|
| `poll_id` | the poll this was derived from |
| `content_sha256` | hash of the post-transform document |
| `transform_id` | fingerprint of the transform rules that produced it |
| `changed` | against the last normalised observation |
| `prev_hash`, `record_hash` | its own chain, separate from the poll chain |

No blob is written for normalised content. The raw response is already
retained and the rules are fingerprinted, so it is re-derivable.

### Raw change and document change are not the same thing

The poll log's `changed` is computed on the response as fetched, which is
correct for retention and misleading as a signal. Real sites move their raw
bytes on every single request — CSP nonces, ASP.NET `__VIEWSTATE`, rotating
banners — while the content you actually selected sits perfectly still.
Measured on a UK procurement watchlist, three of four live targets churned at
the raw level and none of them had moved.

So there are two chains, answering two questions:

```
poll chain          did the bytes we fetched change?
normalisation chain did the content we selected change?
```

`kibitzr archive status` reports both as `raw chg` and `doc chg`, and judges
selectors on the second.

**Where the hash is taken matters.** A pipeline ending in `changes` emits a
diff, and an empty one when nothing moved. Hashing the end of the pipeline
would hash a report about the document rather than the document, and would
record the same value for "unchanged" and "changed back". The capture is
therefore taken at the *input* to the first reporting transform — the
normalised document — or at the end of the pipeline when there is no
reporting transform.

## The hash chain

Each poll's `record_hash` covers its identifying fields plus the previous
poll's `record_hash`, serialised canonically so anyone holding a row can
recompute it. Editing or deleting a logged row breaks the chain from that
point on:

```python
store.verify_chain("Example")                # poll chain
store.verify_normalisation_chain("Example")  # normalisation chain
store.combined_head("Example")               # the value to anchor
```

`combined_head()` is the anchoring seam. There are two chains, and anchoring
one would leave the other free to be rewritten, so it commits to both:

```
sha256('{"norm":"<norm_head>","poll":"<poll_head>","v":1}')
```

with an unanchored chain represented by the all-zero genesis value. Submitting
it for external timestamping anchors every poll recorded up to that point —
one anchor per check per batch, rather than one per observation.

The chains also cross-check each other. Rewriting a normalised hash to conceal
an amendment breaks the normalisation chain while the poll chain still
verifies, and the retained raw response still contains the original text.

This matters because **git history is not evidence**. Kibitzr initialises
each page repo with a hardcoded `user.email` and `user.name`, commits are
unsigned, and commit timestamps come from the local clock — all rewritable
with `git rebase` or `--date`. The chain here is likewise only as good as
its anchor: it makes tampering *detectable by anyone holding an earlier
head*, and nothing more, until that head is committed to an external
timestamp. The anchor is the evidence; this is the thing the anchor points
at.

## Deliberate limits

- **Normalisation is fingerprinted, not versioned.** Every normalised
  observation records a `transform_id` — a hash of the transform rules that
  produced it — so retuning a selector is *detectable*: `status` warns when a
  check has more than one rule set in its series, and the fingerprint is
  inside the chain, so the keeper cannot retune and deny it. But a hash is not
  a history. It tells you the rules changed, not what they were or when they
  were valid. Open Terms Archive solves that properly, with dated declaration
  and filter histories and an explicit `isTechnicalUpgrade` marker.
- **Archiving never fails a check.** A storage error is logged and
  swallowed; monitoring keeps running, and the transform wrapper re-raises
  nothing. Watch the logs rather than assuming silence means success.
- **The `before_start` hook reaches into kibitzr's pipeline.** It wraps one
  callable in `TransformPipeline.transforms`, which is an internal. It
  degrades safely — an uninspectable pipeline logs a warning and records raw
  polls only — but a kibitzr refactor could silently stop normalised hashes
  being recorded. `status` lists checks with no normalised rows for exactly
  this reason.
- **Concurrency is untested.** A normalised row is linked to the most recent
  poll for its check. Kibitzr runs checks sequentially, so this holds today;
  it would not survive a threaded scheduler without passing the poll id
  through explicitly.

## Tests

```bash
pip install -e ".[test]"
pytest tests/
```

The suite covers the store standalone, and end-to-end through kibitzr's
own fetcher selection against a live local server.
