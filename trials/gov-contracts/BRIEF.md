# Brief: get the UK contracts trial collecting

For a Claude Code session running on the laptop. Self-contained — assumes no
prior conversation.

## What this is

A trial of a change-detection archive pointed at UK public procurement. The
goal is to find out whether monitoring award notices produces material worth
publishing — and, specifically, whether **silent amendments** to award notices
can be caught and proved. Values get restated, suppliers change, notices get
withdrawn, and the original is usually not recoverable afterwards.

The output is a tamper-evident archive: every poll logged, every response
retained, hash-chained so the log can incriminate its own keeper.

**Not** a trading system and not a latency race. Nothing here is time-critical.

## What already exists

On branch `claude/kibitzr-review-uotgqa`:

- `tools/kibitzr-archive/` — a kibitzr plugin (not a fork) adding a poll log,
  raw response retention, and a hash chain. 22 passing tests. Installs via the
  `kibitzr.fetcher` and `kibitzr.cli` entry points, so it applies to any
  kibitzr tree.
- `trials/gov-contracts/kibitzr.yml` — ten targets: five recurring UK sources,
  four placeholder slots for individual notices to watch, one direct-awards
  angle.
- `trials/gov-contracts/probe.py` — fetches a URL twice and diffs, to find
  selectors that produce a stable hash.

## What only this machine can do

The session that wrote the above could not reach any `gov.uk` host — network
policy refused them. So **every URL and selector in `kibitzr.yml` is
unverified**, and the selectors marked `PROVISIONAL` are guesses. Expect to
replace all of them.

## Known gotchas — do not rediscover these

*Provenance: revised 1 Aug 2026 on the laptop, against a real kibitzr checkout and
a live PyPI install on Python 3.13.3. The install and promoter-priority items were
asserted rather than executed in the first revision and were both wrong; they are
corrected below. Transform names and the CLI entry-point hook were re-checked
against source and hold. The redirect item remains reasoning, not a test.*

- **Python 3.12+ is fine — install normally.** An earlier revision of this brief
  claimed `sh<2` fails at wheel build on 3.12+ and told you to use Python 3.11
  or `--no-deps`. That is wrong, and following it wastes an hour installing a
  second interpreter. Tested on Python 3.13.3: `pip install kibitzr` exits 0 and
  resolves `kibitzr 8.0.0`, `sh 1.14.3`, `lxml 6.1.1`, `requests 2.34.2`; the
  package imports. `sh<2` picks up 1.14.3, which added 3.12 support. Use your
  system Python.
- **Kibitzr follows redirects silently.** A moved target gets archived under
  its old URL and the poll log then attests to a fetch that did not happen as
  described — the one defect a provenance record cannot carry. An earlier
  draft carried a US target that had moved from `defense.gov` to `war.gov`
  without the config noticing. Resolve every URL to its final host first:
  `curl -sIL -o /dev/null -w '%{url_effective}\n' <url>`
- **Kibitzr hardcodes its User-Agent** in `fetcher/simple.py`. An archival
  crawler making evidential claims should identify itself with a contact URL.
  Patch that line before the archive is load-bearing.
- **Valid transform names** (verified against source, do not guess):
  `css`, `css-all`, `tag`, `text`, `xpath`, `xpath-all`, `json`, `jq`,
  `jinja`, `changes`, `python`, `bash`/`shell`. There is no `cut` or `sort`.
- **`changes` styles:** `default`, `verbose`, `word`, `new`. Note
  `transformer/plain_text.py:18` lowercases the value and hands it straight to
  `PageHistory` without validating it, so a misspelled style fails silently
  rather than erroring. Check the diff output looks right after setting one.
- **`period`** accepts a pytimeparse string (`6h`) or seconds. Alternatively
  `schedule: {every: 6, unit: hours}` or `{every: day, at: "17:30"}` — note
  `at` uses the host's local time.
- **Fetcher promoter priorities: avoid 15, not 20.** An earlier revision of this
  brief said not to register at PRIORITY 20. That is inverted. The underlying bug
  is real — upstream `kibitzr/fetcher/factory.py` does
  `sorted(applicable, reverse=True)[0][1]` on `(PRIORITY, class)` tuples, so a tie
  falls through to comparing classes and raises TypeError. But the shipped
  priorities are `5` (Requests), `10` (base), `15` (Firefox) and `15` (Script), so
  **20 ties with nothing and wins outright — it is the safe value. 15 is the one
  that collides.** The two existing 15s never collide only because their
  `is_applicable` are mutually exclusive on the presence of `url`.
  Note this is upstream-from-PyPI behaviour, which is what task 1 installs. A
  locally hardened checkout elsewhere on the laptop fixes it with
  `max(key=...)`; that fix does **not** travel into this venv.

## Tasks, in order

### 1. Install

```bash
git fetch origin claude/kibitzr-review-uotgqa
git checkout claude/kibitzr-review-uotgqa
python3 -m venv .venv && . .venv/bin/activate
pip install kibitzr
pip install -e tools/kibitzr-archive
cd trials/gov-contracts
```

**Done when:** `kibitzr archive --help` lists `status`, `verify`, `head`.
That proves both entry points resolved.

### 2. Resolve canonical URLs

For all ten targets, follow redirects and record the final URL. Correct
`kibitzr.yml` where they differ. The two OCDS API paths (Contracts Finder,
Find a Tender) are the most likely to be wrong — they have moved before, and
the UK notice regime changed under the Procurement Act during 2025, so query
parameters may also need updating.

**Done when:** every URL returns 200 with no redirect, or the config records
the redirect target instead.

### 3. Tune selectors

Per target:

```bash
./probe.py <url> --suggest                  # what containers exist
./probe.py <url> --selector "main"          # stable across two fetches?
./probe.py <url> --selector "main" --text   # ignore markup churn
```

Exit 0 = stable, 2 = unstable. Anything differing across two fetches seconds
apart is churn — session tokens, rotating banners, "generated at" stamps — and
must be selected away or it registers as a change on **every poll**.

This step decides whether the archive grows at ~60 KB per target per year or
at gigabytes, and whether you get two useful alerts a week or fifty useless
ones a day. It is the most important step here. Do not skip it to get to a
running system faster.

For the JSON/OCDS targets, tune the `jq` expression instead: reduce each
release to the fields that matter so unrelated payload churn does not register.

**Done when:** every target reports STABLE, or is documented as unstable with
the reason. A target that cannot be stabilised is a finding, not a failure —
record it and drop it.

### 4. Fill the watched-notice slots

Replace the four `REPLACE ME` placeholders with real notice URLs — recent,
large or contested awards, ideally spread across central government, NHS,
defence and local authority. These are the highest-value targets: an
amendment caught here is a story on its own. Keep their `period` tight for the
first month, since amendments cluster shortly after publication.

### 5. First collection

```bash
kibitzr once
kibitzr archive status
```

**Done when:** `status` shows one poll per target, changes equal to the number
of targets (each first observation counts as a change), and `archive/blobs/`
holds one retained response per target.

### 6. Schedule it

On a laptop, cron or a user-level systemd timer / launchd agent is fine. Run
`kibitzr` (not `once`) under a supervisor, or invoke `kibitzr once` on a timer
— the latter survives sleep better.

**Sleep gaps are fine and are recorded correctly.** The poll log distinguishes
"checked, unchanged" from "not watched", which is the entire reason it exists.
A laptop that sleeps produces an honest archive with visible gaps, which is
strictly better than a silent one.

Defer the VPS until the trial says the data is worth £5/month.

### 7. Replicate offsite

Non-negotiable before this accumulates value. The blob store is
content-addressed and append-only, so it syncs cleanly:

```bash
rclone sync archive/ remote:uk-contracts-archive/
```

Anything durable works — B2, R2, or a git remote. The point is that the
machine holding the only copy becomes disposable.

### 8. Run for two weeks, then review

```bash
kibitzr archive status     # per-target polls, changes, noisy-target warning
kibitzr archive verify     # recompute every hash chain, non-zero on failure
kibitzr archive head       # chain heads, for submitting to a timestamp
```

`status` flags any target changing on more than half its polls. That is a
broken selector, not a busy source.

## What success looks like

- **Recurring sources:** changes correspond to genuinely new awards. A target
  changing on most polls has a selector problem.
- **Watched notices:** flat. Any change is a potential story, and the first
  amendment caught validates the whole exercise.
- **All chains verify.**

## What not to build yet

No web app. The data changes a few times a day at most, so when it is time to
publish, static generation into the existing Vercel site beats a dynamic app —
no runtime, no new public surface, no extra hosting.

The failure mode to avoid is spending weekends on a front end while the
selectors go untuned. Clean data with no UI is a success; a polished UI over
churning garbage looks like progress for months and is worth nothing.
