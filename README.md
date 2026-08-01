# evidence-archive

Tools and trials for change-detection archives that can be checked by someone who
does not trust the archivist.

Existing archives — the Wayback Machine, Open Terms Archive, a git-backed monitor —
answer *what did this page say, and when*. They do not answer *how would I know
that independently*. Commit dates are author-supplied, git history can be
regenerated, and a third-party archive rests on trusting the third party. The
claim this repo is built around is narrower and defensible: an archive should be
able to incriminate its own keeper.

Three properties, none of which the underlying tools provide:

- **Integrity** — the content you hold is the content that was recorded.
- **Time** — it was recorded when it says it was.
- **Continuity** — no record between two others has been removed.

## Layout

### `tools/kibitzr-archive/`

A plugin for [kibitzr](https://github.com/kibitzr/kibitzr), not a fork. It adds
the three things a monitor needs before its output is evidence:

- **A poll log.** Every poll is recorded, not only the ones that changed. Without
  this, silence cannot distinguish *checked, unchanged* from *not watching* from
  *fetch failed* — and a gap in coverage looks identical to a period of genuine
  stability.
- **Raw response retention.** The response is kept as fetched, before the
  transform chain touches it, so a third party can re-derive the extraction
  instead of taking the extracted text on trust. The blob store is
  content-addressed, so re-observing content already held costs nothing.
- **A hash chain.** Each poll row's `record_hash` covers its own fields plus the
  previous row's hash. Anchoring the latest hash anchors the whole history behind
  it, which is where an external timestamp proof attaches.

Registers through kibitzr's `kibitzr.fetcher` and `kibitzr.cli` entry points, so
it applies to any kibitzr tree without patching it.

```
kibitzr archive status     # per-check polls, changes, last observation
kibitzr archive verify     # recompute every chain; non-zero exit if broken
kibitzr archive head       # chain heads — the values to submit for timestamping
```

22 tests, including tamper detection. Verified on Python 3.13.3.

### `trials/gov-contracts/`

A trial pointed at UK public procurement, asking whether monitoring award notices
catches **silent amendments** — values restated, suppliers changed, notices
withdrawn, with the original usually unrecoverable afterwards.

`BRIEF.md` is the operating brief. `kibitzr.yml` is the watchlist.
`probe.py` fetches a URL twice and diffs, to find selectors that produce a
stable hash.

**Status: not yet collecting.** Every URL and selector in `kibitzr.yml` is
unverified — the environment it was written in could not reach `gov.uk` hosts.
Selectors marked `PROVISIONAL` are guesses. Selector tuning is the step that
decides whether this produces two useful alerts a week or fifty useless ones a
day; it is not a formality.

The four watched-notice slots are deliberately unfilled placeholders. Inventing
notice IDs would be worse than leaving them blank.

## A note on publishing the watchlist

This repo is public while the watched-notice slots are empty, which costs
nothing. That changes once they are filled: publishing which specific award
notices you are watching for amendment tells the awarding bodies they are being
watched, and quiet amendment is precisely the behaviour that stops when observed.
Consider keeping filled slots out of the public config, or accepting the trade
knowingly.

## Provenance

Split out of `distributed-ethics-site2` with history preserved — it had been
developed on an unmerged branch there, alongside a Vercel-deployed site it has
nothing to do with. Commit hashes differ from the originals because the history
was rewritten to drop unrelated paths; the six commits, their messages, authors
and dates are otherwise intact.
