# evidence-archive

Tools and trials for change-detection archives that can be checked by someone who
does not trust the archivist.

Existing archives — the Wayback Machine, Open Terms Archive, a git-backed monitor —
answer *what did this page say, and when*. They do not answer *how would I know
that independently*. Commit dates are author-supplied, git history can be
regenerated, and a third-party archive rests on trusting the third party. The
claim this repo is built around is narrower and defensible: an archive should be
able to incriminate its own keeper.

The archive adds three properties that the underlying monitor does not provide:

- **Integrity** — the content you hold is the content that was recorded.
- **Time** — an externally timestamped head bounds when the records behind it
  already existed.
- **Continuity** — no record between two others has been removed.

The wording matters. A hash chain alone establishes integrity and continuity,
not time. Time begins at an upgraded OpenTimestamps proof, and only for records
covered by that proof. It does not establish that the source was truthful or
that the collector ran when no poll was recorded.

## Current status

Collection of six checks began on 1 August 2026. The running stack is
`kibitzr 8.0.0` from PyPI plus version 0.2.1 of this plugin; it is not a private
fork of kibitzr. Version 0.2.0 is the first release whose verifiers bind retained
responses and anchor manifests to values outside the mutable database in the
way the verification specification requires.

The public [control target](https://github.com/petesherratt-collab/evidence-control)
changes independently of the collector and exercises the end-to-end fetch,
decode, hash, change-detection and recording path. A point-in-time
[off-site snapshot](https://github.com/petesherratt-collab/evidence-archive-snapshot)
provides a separately stored copy while permanent object-storage backups are
being established.

## Layout

### `tools/kibitzr-archive/`

A plugin for [kibitzr](https://github.com/kibitzr/kibitzr), not a fork. It adds:

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
- **A normalisation chain.** The selected or transformed document is committed
  separately from the raw response, so raw page churn can be distinguished from
  a meaningful document change.
- **An annotation chain.** Corrections, schedule declarations and regime changes
  are append-only evidence rather than mutable notes beside the archive.
- **External anchors.** OpenTimestamps proofs bind manifests of current chain
  heads to Bitcoin attestations once upgraded.
- **Fetcher containment.** The requests path rejects private-network targets and
  proxies by default, pins vetted DNS results, bounds redirects, response size
  and wall-clock time, and records one target's failure without stopping the
  remaining checks.

Registers through kibitzr's `kibitzr.fetcher` and `kibitzr.cli` entry points, so
it applies to any kibitzr tree without patching it.

```
kibitzr archive status       # polls, raw/document changes, last observation
kibitzr archive verify       # recompute the database chains
kibitzr archive fsck         # check blobs, manifests, proofs and backup heads
kibitzr archive gaps         # compare observed polls with declared schedules
kibitzr archive anchors      # proofs taken and polls not yet covered
kibitzr archive calibration  # measure control-target observation lag
kibitzr archive report       # write a static, hash-backed evidence browser
```

For a browser-friendly view, run this from the collection root and open the
resulting directory's index:

```sh
kibitzr archive report --root archive --output report
```

The report reads the archive without modifying it. It separates raw response
changes from selected-document changes and links hash-verified changes to
before/after evidence pages. It works offline and makes no requests to
third-party services. Run the command again whenever you want to refresh the
display. See the plugin README for reconstruction limits and the publication
privacy warning.

The current suite contains 173 tests, including forged-archive and tamper cases,
and passes on Python 3.13.3:

```sh
python3 -m pip install -e 'tools/kibitzr-archive[test]'
python3 -m pytest tools/kibitzr-archive/tests -q
```

See [`tools/kibitzr-archive/README.md`](tools/kibitzr-archive/README.md) for the
data model and CLI, [`deploy/README.md`](deploy/README.md) for operation and
backup, and [`deploy/VERIFYING.md`](deploy/VERIFYING.md) for the verification
specification and its limits.

### `deploy/`

User-level systemd units, bounded network-readiness checks, anchoring and backup
automation live here. `verify_independently.py` reimplements the verification
specification using only Python's standard library and imports no plugin code.
Agreement between it and the plugin is useful cross-checking; neither replaces
checking the OpenTimestamps proof against its Bitcoin attestation.

### `trials/gov-contracts/`

A trial pointed at UK public procurement, asking whether monitoring award notices
catches **silent amendments** — values restated, suppliers changed, notices
withdrawn, with the original usually unrecoverable afterwards.

`BRIEF.md` is the operating brief. `kibitzr.yml` is the watchlist.
`probe.py` fetches a URL twice and diffs, to find selectors that produce a
stable hash.

The original provisional watchlist has since been reduced to six live checks:
five UK procurement or spending sources plus the external control target.
Selectors were tuned against live responses. Three sources still change at the
raw-response level on every request because of page-generated nonces while their
selected document region remains stable; this is why status reports `raw chg`
and `doc chg` separately.

No individual watched-notice slots are currently published. Adding them remains
a deliberate editorial and operational decision, not a setup placeholder.

## Verify an archive

Run both local checks: they cover different obligations.

```sh
kibitzr archive verify --root /path/to/archive
kibitzr archive fsck   --root /path/to/archive
python3 deploy/verify_independently.py /path/to/archive
```

`verify` recomputes the poll, normalisation and annotation chains in
`polls.db`. `fsck` additionally checks every referenced retained response,
manifest, proof file and recorded backup head. The independent verifier starts
at the digest embedded in each detached `.ots` proof, follows it through the
manifest, and recomputes the database chains rather than trusting stored head
values.

These offline checks establish which bytes a proof covers, but not when they
were attested. For every manifest, also run `ots verify anchors/<manifest>.ots`
and compare the reported Bitcoin block time with the manifest's `created_at`.
A pending calendar proof is not yet a Bitcoin timestamp.

## A note on publishing the watchlist

This repo is public while the watched-notice slots are empty, which costs
nothing. That changes once they are filled: publishing which specific award
notices you are watching for amendment tells the awarding bodies they are being
watched, and quiet amendment is precisely the behaviour that stops when observed.
Consider keeping filled slots out of the public config, or accepting the trade
knowingly.

## Provenance

Originally split out of `distributed-ethics-site2` with its six relevant commits
preserved. Commit hashes differ from their originals because unrelated paths
were removed during the split. Subsequent collection, verifier and deployment
work was developed in this repository.
