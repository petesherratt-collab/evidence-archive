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

### How the fetch is made

An archived fetch does not use kibitzr's session as built. The plugin
replaces it, because several of the stock behaviours would let the archive
record something that is not true. Each is a statement the record makes on
your behalf, so each is listed here rather than left to the source.

- **Every poll reaches the origin.** kibitzr wraps its session in
  `CacheControl`, and the fetcher is constructed once per check and lives for
  the process. A target serving a long `max-age` would have its polls answered
  from an in-memory cache, and those polls would enter the log as observations
  indistinguishable from genuinely unchanged ones — collapsing the exact
  distinction this archive exists to keep. The cache is removed and
  `Cache-Control: no-cache, no-store` sent, which covers intermediaries too.
- **Redirects are followed one vetted hop at a time.** requests follows them
  internally, which would put every hop after the first outside any check. A
  cross-origin redirect is refused by default: the poll row records the
  *configured* URL, so following one would file another site's content under
  this check's name.
- **Connections go to the address that was vetted.** The hostname is resolved
  once to decide whether the target is public; without pinning, requests
  resolves it a second time when it opens the socket, and the name that was
  checked and the address that is reached are two different facts. A host with
  no vetted address is refused rather than resolved. `Host` and SNI still carry
  the hostname, so certificate validation is unaffected.
- **A proxied fetch is refused** unless `allow_proxy` is set. Under a proxy the
  target is resolved at the far end of the connection, where pinning cannot
  reach — and requests picks proxies up from `HTTP_PROXY`/`HTTPS_PROXY` without
  the config mentioning them, so this would otherwise degrade silently.
- **The fetch is bounded** in body size and in wall-clock time, redirects and
  retry backoff included. kibitzr runs checks on one thread, and its own
  backoff for a timeout is `60 * (retry + 1)`, so an unbounded fetch is not
  local to one check — it stalls every other one behind it.
- **A bad charset degrades rather than fails.** A page that serves undecodable
  bytes, or names a codec that does not exist, would otherwise be able to make
  itself un-archivable with a non-retriable error. Response-supplied encodings
  fall back to UTF-8 with replacement and a warning; a *configured* `encoding`
  that does not decode stays loud, because that one is your error, not the
  page's.

Defaults, all per check:

```yaml
    max_response_bytes: 10485760   # 10 MB; larger responses are refused
    max_fetch_seconds: 60          # whole fetch, redirects included
    max_retry_seconds: 90          # total sleep across retries
    max_redirects: 5
    minimum_content_bytes: 1       # shorter successful bodies fail the poll
    allow_private_network: false   # LAN/loopback/link-local targets
    allow_cross_origin_redirects: false
    allow_proxy: false
```

The three `allow_*` settings are refusals by default and assertions when set:
each one says you have accepted that the address reached may not be the
address vetted. Turning one on is a change to what a successful poll means, so
it belongs in a `fetch_regime` annotation — see
[Knowing when the instrument changed](#knowing-when-the-instrument-changed).

This whole section applies to the requests path only. A check routed through
Firefox uses kibitzr's browser fetcher unchanged: no pinning, no ceilings, no
redirect vetting, and its own cache. The poll is still logged and the response
still retained.

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

So there are three chains, answering three questions:

```
poll chain          did the bytes we fetched change?
normalisation chain did the content we selected change?
annotation chain    what do we now know about the log that it does not say?
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
store.verify_annotation_chain()              # annotation chain (global)
store.combined_head("Example")               # the value to anchor
```

`combined_head()` is the anchoring seam. Anchoring one chain would leave the
others free to be rewritten, so it commits to all three:

```
sha256('{"ann":"<ann_head>","norm":"<norm_head>","poll":"<poll_head>","v":2}')
```

with an unanchored chain represented by the all-zero genesis value. Submitting
it for external timestamping anchors every poll recorded up to that point —
one anchor per check per batch, rather than one per observation.

The chains also cross-check each other. Rewriting a normalised hash to conceal
an amendment breaks the normalisation chain while the poll chain still
verifies, and the retained raw response still contains the original text.

### What the chains do not cover

All three chains recompute from `polls.db` and nothing else. That is the right
scope for what they claim — they detect an edited or truncated *log* — but it
has a consequence worth stating plainly, because the failure it permits looks
exactly like success:

**An archive with an empty `blobs/` and no `anchors/` passes `verify` with
every chain intact.** Nothing is wrong with the chains. The retained responses
and the proofs were simply never inside them. `raw_ref` is folded into a record
hash, so the log can prove the *reference* was not tampered with, and can say
nothing whatever about whether the file it names still exists.

That is the expected result of a copy to removable media that filled up or was
unplugged part-way, which is the moment someone is relying on the answer. So
there is a second command, and both must pass:

```
kibitzr archive verify   # the three chains — polls.db alone
kibitzr archive fsck     # blobs and proofs — what no chain reaches
```

`fsck` checks that every `raw_ref` resolves to a file, that every blob's bytes
hash to the name it is filed under (content addressing makes that the whole of
blob integrity), that every anchor's manifest is present and still matches the
digest its proof was taken over, and that every recorded proof file exists.

It also checks the one seam with no counterpart anywhere else: an anchor names
a `last_poll_id` and the head it committed to, so if the log no longer produces
that head at that row, the log and the proofs beside it are **from different
moments**. That is the signature of a `polls.db` restored from an older copy
than the `anchors/` next to it — and both halves are internally consistent, so
every chain verifies and every proof still validates against its manifest.

Findings are graded. `BROKEN` means something is gone or contradictory that
cannot be re-derived, and exits non-zero. `note` means an inconsistency with no
evidence provably lost: an orphaned blob left by a crash between `put_blob` and
its INSERT is mess rather than damage, and polls not yet covered by a proof are
the state every archive is in for most of its life. Grading those as failures
would make `fsck` fail continuously and therefore be ignored, which is how a
loud check becomes a decoration.

## Correcting the record

A poll row can be wrong about the world. The collector's own failures are
recorded in the same log as its observations, and a bug in failure handling
puts a false statement on the chain — one that is now, by construction,
unrewritable.

That is the design working, not a problem with it. The correction goes on the
annotation chain and is read alongside the rows it describes:

```
kibitzr archive annotate --kind correction --check "Example" \
    --from-poll 11 --to-poll 40 \
    --effective-from 2026-08-02T06:51:00+00:00 \
    --detail '{"true_cause": "..."}'
kibitzr archive annotations --kind correction
```

The rows named keep their original text and still verify. Annotations are
themselves chained and covered by the anchor, so a correction cannot be
withdrawn any more quietly than a poll can be doctored.

`effective_from` (when the fact became true) is stored separately from
`recorded_at` (when we wrote it down), so a retrospective annotation cannot
imply we knew earlier than we did.

Three kinds are written automatically or by hand:

| kind | says |
|---|---|
| `correction` | rows N..M record X; the truth was Y |
| `fetch_regime` | from time T the fetcher behaves like this |
| `schedule` | check C was *intended* to poll every P seconds from time T |

## Knowing when the instrument changed

`transform_id` makes a selector retune detectable. `fetch_id` does the same for
the other half of the pipeline, and is recorded on every poll row.

It exists because fixing the fetcher changes *when a poll counts as failed*
without changing a single collected byte. Failure counts either side of such a
change are not comparable, and without a fingerprint a reader would have to
correlate against a git history they may not hold to discover that. `status`
warns when a check's series spans more than one regime.

Rows written before the fingerprint existed carry no `fetch_id`, and are
excluded from the record hash when absent — so archives predating it still
verify. They still count as a regime of their own, named by a retrospective
`fetch_regime` annotation rather than by a value on the row.

## Reading a gap

Every poll writes a row, so silence in the log already means nobody looked —
that invariant is the reason the poll log exists at all.

What silence *cannot* say on its own is whether anyone meant to be looking. A
hole is ambiguous between "not scheduled yet" and "scheduled, and the machine
was off", and only the second is a gap in coverage. So the intended period is
recorded as data:

```
kibitzr archive gaps
```

Each interval is judged against the schedule in force when it opened, not the
current one. Checks with no schedule declared are listed separately as
unjudgeable rather than reported as fine.

## Controls, and why an archive needs one

Everything above makes unchanged polls *recordable*. None of it makes them
*believable*, and those are different problems.

Over a fortnight on stable sources you expect few or no document changes. A
quiet archive is then consistent with two very different worlds:

1. Nothing changed — a real null, and informative.
2. The pipeline broke silently — a selector matching nothing and returning
   empty every time, a response served from cache, a transform throwing into a
   swallow.

From inside the archive those are the same rows. Worse, the second world is
*more* likely to look tidy than the first, because a broken pipeline produces
perfectly regular unchanged polls with no failures at all.

A control closes it. Mark a check `control: true` and it is asserted on the
annotation chain as an instrument rather than a target:

```yaml
  - name: Control — collector liveness
    url: https://example.invalid/ticker
    period: 1h
    archive: true
    control: true
    transform:
      - css: main
      - text
      - changes: verbose
```

Point it at a page you publish and change on a schedule faster than you poll
it. Then its document changing on every poll is the working state, and
`status` inverts for that check: instead of warning that it changes too often,
it shouts when it stops.

```
*** CONTROL STALLED — the pipeline may be broken ***
  Control — collector liveness: 3 consecutive polls with no document change
    last change 2026-08-03T09:31:33+00:00
```

This is not an uptime check. A liveness probe tells you a process is running.
A control runs fetch → transform → hash → chain → store through the same code
path as a real check, with the same selector mechanism and the same storage,
which is why it belongs in `kibitzr.yml` as an ordinary check rather than in a
monitoring sidecar.

Four things make the difference between a control that works and one that
merely looks like it does:

- **The change must land inside the selected region.** A control page that
  changes only where the selector strips proves nothing while reporting
  healthy. This is the easy one to get wrong.
- **Put deliberate noise outside the selection too.** A build nonce in a
  footer the selector excludes. A normalisation contract has two halves —
  select the signal, discard the churn — and a control exercising only the
  first is half a control. It also makes the control resemble the real targets,
  most of which churn at the raw level on every request.
- **Host it independently of the collector.** A control on the collecting
  machine dies with the collector and teaches you nothing.
- **Put a machine-readable generation timestamp in the content.** Not just a
  counter. See calibration below — and note that it is also what tells you
  which end broke when the control stalls: a lag that has grown past the
  page's publishing interval means the page stopped being rebuilt and the
  collector is fine.

### The limit

A control proves the pipeline works for a page shaped like the control. It
will not catch a target restructuring its HTML so that *that* check's selector
silently matches nothing. Necessary, not sufficient; per-target review still
matters. What it does is convert the most dangerous failure — the silent one —
into a loud one.

## Calibrating observation resolution

```bash
kibitzr archive calibration --check "Control — collector liveness"
```

The configured poll period is a floor on how tightly you can bracket a change,
not the real figure. Scheduler drift, retries and fetch time all widen it, and
none of them appear in the period.

A control is the only target whose true change time is known, because it
publishes it. Comparing that against `polled_at` measures the real width:

```
  47 of 48 retained responses carried a generation time
  min    12s
  median 7.4m
  max    16.2m   at 2026-08-03T11:31:32+00:00
```

It reads the generation time back out of the *retained responses*, so it works
retroactively over everything already collected and needs no extra column. The
`--pattern` option takes a regex with one capturing group, defaulting to
`datetime="([^"]+)"`.

This matters beyond the control. Any claim of the form "this notice changed
between X and Y" is exactly as strong as the bracket around it, and quoting the
configured period there would overstate the archive's resolution. The measured
figure does not transfer to other checks as a measurement, but it does as an
order of magnitude — they run through the same scheduler.

A negative lag is called out separately: it means the page claims to have been
generated after it was observed, which is two clocks disagreeing and sets a
floor on how tightly any bracket can honestly be stated.

## Anchoring

```
kibitzr archive anchor           # commit current heads to an external timestamp
kibitzr archive anchors          # proofs taken, and what no proof covers yet
kibitzr archive anchor-upgrade   # calendar attestation -> Bitcoin attestation
kibitzr archive anchor-verify    # check a proof still holds
```

Anchoring uses OpenTimestamps rather than an RFC 3161 authority, because a TSA
token is only as good as continued trust in that authority, and importing a new
party to trust is the wrong shape for an archive built to remove that need.

What gets stamped is a **manifest**, not a bare digest: canonical JSON naming
each check and its constituent chain heads, so someone holding the proof can see
what was attested without running any of this code. The component heads are
recorded alongside `combined_head` so an anchor stays checkable even after the
combining formula changes version.

Proofs arrive **pending** — attested by the calendar servers — and become
Bitcoin-attested once a block confirms, hours later. `anchors` reports which
state each proof is in and never describes a pending proof as complete.

`anchors` also reports how many polls no proof covers yet. That number is the
archive's actual exposure: those observations have no external evidence of when
they existed, and unlike a missed poll, elapsed unattested time cannot be
recovered afterwards.

The verification specification is `deploy/VERIFYING.md`, and
`deploy/verify_independently.py` implements it using only the standard library,
sharing no code with this package.

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
- **Fetch regimes are fingerprinted, not versioned either.** `fetch_id` has
  exactly the same limit as `transform_id`: it says the fetcher changed, not
  what it was before. The `fetch_regime` annotation is where the prose goes,
  and it is written by hand.
- **A declared schedule is intent, not a guarantee.** It records what the
  collector was *told* to do. It cannot show that the process was up, so a
  series with no gaps and no uptime evidence still rests on trusting the
  keeper. Rules pinned to a wall-clock time do not reduce to a period and are
  reported as unjudgeable rather than guessed at.
- **The retry-loop fix is a local override.** `sleep_on_exception` is
  reimplemented in the promoter because upstream's uses `collections.Callable`,
  removed in Python 3.10. Patching the installed kibitzr instead would be lost
  on the next reinstall, taking the archive's failure attribution with it. If
  upstream fixes it, this override becomes redundant but stays harmless.
- **The fetch boundary is narrow on purpose.** It is not a general SSRF
  defence for arbitrary URLs — the targets are a short list you wrote. What it
  prevents is a *configured* target silently becoming a different one, through
  a redirect or through a lookup that answers differently the second time. It
  also stops at the requests path: a Firefox-routed check keeps none of it.
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
