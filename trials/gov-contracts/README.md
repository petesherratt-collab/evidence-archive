# UK government contract awards — trial

A ten-target watchlist for monitoring public procurement awards, using
`kibitzr` with the `kibitzr-archive` plugin from `tools/kibitzr-archive`.

The output is intended as material, not signal. The question this trial
answers is whether a change-detector pointed at procurement produces a
repeatable format — not whether it predicts anything.

## Read this first

**Nothing here has been fetched.** The environment this was written in cannot
reach `contractsfinder.service.gov.uk`, `find-tender.service.gov.uk`,
`crowncommercial.gov.uk`, `publiccontractsscotland.gov.uk` or `gov.uk` —
outbound requests to those hosts are refused by network policy. So every URL
is unverified and every selector marked `PROVISIONAL` is a guess.

That is not a formality. Expect to replace all of the selectors and to correct
at least some of the URLs, particularly the two OCDS API paths, which have
moved before. `probe.py` exists to make that quick.

## Why the targets are what they are

**1–5, recurring sources.** Contracts Finder carries the volume; Find a Tender
carries the higher values and so the better story-per-notice ratio. Public
Contracts Scotland is included because below-threshold Scottish material never
reaches the UK-wide feeds, which makes it both additive and less watched.
Departmental spend over £25k is the odd one out and possibly the best: it
records what was actually *paid* rather than what was announced.

All five use OCDS APIs or stable publication pages rather than HTML search
results, which churn on every request and would produce noise rather than
signal.

**One structural difference to plan around.** There is no UK equivalent of a
daily, packaged, named-winner announcement — the US defence department
publishes one each weekday at 5 p.m. ET, and nothing here does. UK procurement
publishes as raw notice flow. So the editorial shaping that would have come
free is yours to do. That is a real cost, and also why the space is open.

**6–9, individual award notices.** The highest-value block, and the reason
the archive plugin exists. Procurement records get quietly revised — values
restated, suppliers changed, notices withdrawn — and the original is usually
not recoverable afterwards. Catching one is a piece by itself, and it is
where the hash chain does real work: you can show what the register said on
the day, and prove you are not the one who changed it.

These are deliberately left as placeholders. Fill them with four recent, large
or contested awards, ideally spread across central government, NHS, defence
and local authority. Poll them tightly for the first month — amendments
cluster shortly after publication.

**10, direct awards.** Awards made without competition are a defined,
findable category. The pattern is the story, so it needs no scoop.

## Running it

```bash
pip install kibitzr
pip install -e ../../tools/kibitzr-archive
```

Then, per target, before trusting anything:

```bash
./probe.py <url> --suggest                    # what containers exist?
./probe.py <url> --selector "main"            # is that region stable?
./probe.py <url> --selector "main" --text     # ignore markup churn
```

`probe.py` fetches twice a few seconds apart and diffs the results. Anything
that differs across two requests seconds apart cannot be a real change at the
source — it is churn, and it must be selected away or it will register as a
change on every single poll. Exit code is 0 for stable, 2 for unstable, so it
scripts.

Iterate until stable, put the working selector in `kibitzr.yml`, then:

```bash
kibitzr once      # single pass over all checks
kibitzr           # run on schedule
```

## What success looks like

Different from a policy-drift run, where the goal is zero changes. Here you
want changes — the question is whether they are *legible*.

- **Sources 1–5:** changes should correspond to genuinely new awards. If a
  target changes on most polls, its selector is wrong, not the source.
- **Sources 6–9:** should be flat. Any change is a potential story, and the
  first amendment you catch validates the whole exercise.
- **Source 10:** steady accumulation.

After two weeks, check `store.stats()` per target. A target producing far more
changes than the underlying documents plausibly have is a normalisation
defect — catch it there, not in the notification stream.

```python
from kibitzr_archive.store import ArchiveStore
store = ArchiveStore("archive")
store.stats("Contracts Finder — recent awards")
store.verify_chain("Contracts Finder — recent awards")
```

## Known gaps

- **User-Agent is not wired through.** Kibitzr's simple fetcher hardcodes its
  own (`fetcher/simple.py`), so the identifying UA in the config comment has
  no effect yet. An archival crawler making an evidential claim should say who
  it is; either patch that line or accept the default for the trial.
- **Selectors are not versioned.** Change one and the extracted text changes
  for reasons unrelated to the publisher. The archive plugin retains raw
  responses so extraction can be re-derived after the fact, which mitigates
  this but does not solve it.
- **UK notice types changed** under the Procurement Act during 2025, so query
  syntax and notice structures may not match older documentation, and
  backfilling against historical data will not line up cleanly.
- **Redirects are followed silently.** Kibitzr's fetcher follows them by
  default, so a target that has moved will be archived under its old URL —
  the log attests to a fetch that did not happen as described. Resolve each
  URL to its final host before adding it, and re-check the watchlist
  periodically. An earlier draft of this config carried a US defence target
  that had moved from `defense.gov` to `war.gov` without the config noticing —
  the failure is silent, which is what makes it dangerous.
