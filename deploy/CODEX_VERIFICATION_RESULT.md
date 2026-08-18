# Evidence Archive verification result

Captured 2026-08-18T09:37:28Z from branch `codex/integrated-public-browser`,
base commit `0bc0abcf2dc8de3e2f608900a109f7ebceb07688`. The base commit is
present on GitHub; the verification changes below are local and uncommitted.

## Archive and snapshot

Live archive: /home/peter/evidence-collection/archive
Archive identity at capture: polls.db SHA-256
dc6189a8e328728550ea7a0efe7b7bfa132234a915579a3c24d3e56dfb70d908
Live archive at capture: 444 polls, 401 normalisation rows, 39 annotation rows,
and 129 recorded anchors
Checked-in historical snapshot export ID:
export-adc53ae73a759cd128df9575be0cb759
Snapshot generated at 2026-08-14T06:32:38+00:00
Snapshot covers 288 observations and 1,888 procurement records
Snapshot contains 2,099 recorded changes under its original publication
semantics. Of those, 141 are legacy source-level control events:
70 raw_response_changed and 71 content_changed. These control events have
no procurement record_id and remain excluded from current procurement BI
activity semantics.

The snapshot's authenticated chain heads resolve inside the later append-only
live archive to poll heads 283, 288, 273, 284, 272, and 274 for Contracts
Finder, control, Departmental spend, Find a Tender, GCA, and direct awards
respectively. Their normalisation heads resolve to 241, 246, 231, 242, 230,
and 232, and all share annotation head 39.

Historical verification now reconstructs the archive state exactly through
those authenticated heads and independently reproduces the original checked-in
snapshot, including its legacy control-event publication semantics. Later
archive rows are ignored only because they occur after the authenticated cut;
current-state verification remains strict against the full live archive, which
continues through poll 444.

## Verification

- Historical mode passed against the live archive. It independently
  reconstructed the 1,888-record snapshot through the authenticated cut and
  matched the published projection exactly.
- Ordinary current-state mode passed against a freshly generated export from
  the entire live archive: 2,435 procurement records.
- Historical mode has no arbitrary poll-number or `--ignore-after` escape
  hatch. Current mode remains whole-archive verification.
- Existing fully rehashed truncated-export and fully rehashed forged-event
  attacks remain rejected.
- Verifier adversarial suite: 38 passed, including historical-cut,
  legacy-profile binding, forged-cut, chain-continuity, semantic-integrity,
  and current control-event rejection coverage.

## Regression checks

- Canonical Python suite: 278 passed.
- Node/browser tests: 2 passed.
- Python compile: passed.
- JavaScript syntax checks: passed.
- `git diff --check`: passed.
- `archive fsck --strict --allow-unanchored`: passed on a byte-for-byte copy
  of the live archive because the live archive database is read-only to the
  installed CLI. The copy reported 371 blobs referenced and present, 129
  anchors, and 14 unanchored polls. Unanchored fresh observations are a
  coverage state, not corruption.

## Collection gap 164 to 165

Poll 164 succeeded at `2026-08-09T19:34:41Z`; poll 165 succeeded at
`2026-08-11T13:41:26Z`, a gap of 42 hours 6 minutes 45 seconds. The retained
control pages move from generated `2026-08-09T19:09:34Z`, sequence 125, to
`2026-08-11T13:38:34Z`, sequence 166. This establishes a long publisher gap.

Retained service journal evidence shows a later collector start at 15:05 BST,
an orderly first full cycle, a stop with exit status 1 at 16:43 BST, and a
restart with DNS recovery and another full cycle beginning at 16:50 BST. The
retained health journal begins at 16:59 BST; it does not establish the outage
onset or its full duration. Chain continuity proves that the archive resumed
correctly. It does not prove that no upstream changes occurred while
collection was down. No historical evidence was rewritten, repaired,
renumbered, or synthesized.

## Public browser

The integrated browser layout was previewed and its real checked-in data was
exercised through search, filtering, stable sorting, pagination, CSV export,
buyer, supplier, contract, and evidence links. The flow found zero control
events in procurement activity, preserved separate EUR/GBP/USD totals, and
passed formula-marker protection including Unicode whitespace/control prefixes.
The public export includes the per-source observation payloads and record
evidence IDs.

There is no separate Methodology page or navigation entry in the current
integrated browser; this remains an unresolved UI finding. The headless
overview capture also rendered before its large asynchronous JSON payload had
finished populating, while the deterministic selectors and export-integrity
tests passed against the same data.

Pending `.ots` files are not treated as Bitcoin proof. Production promotion
was not performed.
