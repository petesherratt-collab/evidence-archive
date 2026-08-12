# Codex itinerary — adversarial review follow-up

Derived from a pre-deployment review of `6f21023`..`f1dd96d` plus the current
uncommitted worktree, on 9 Aug 2026. Companion to `CODEX_KONSOLE_ITINERARY.md`,
which still holds the mission framing and the clean-machine install rehearsal.

Start in:

```sh
cd /home/peters/evidence-collection/repo
```

## State at the time this was written

- HEAD is `f1dd96d`; the hardening described below as "already done" is in the
  **uncommitted worktree**, not in any commit. Do not assume a clean tree.
- Live archive: 164 polls, 113 blobs, 31 anchor files. `verify` passes,
  `fsck --strict` passes with no suspect findings, 220 tests pass.
- Laptop is safe: collector disabled and not running, anchor timers disabled,
  backup/health/integrity/heartbeat timers not installed, no collector process.

## Safety boundaries

- Never run two collectors against the same authoritative archive.
- Do not mutate historical evidence. Corrections are appended, never applied
  in place, and not at all until an off-machine backup exists.
- Do not enable production collection or timers on this laptop.
- Do not invent credentials, endpoints or backup destinations.
- Re-run `fsck --strict`, not plain `fsck`, at every gate.

## Do not redo — verified already fixed

Confirmed present and correct in the worktree. Do not spend time here.

- `Persistent=` removed from `evidence-health.timer`; retained correctly on the
  calendar-based `evidence-integrity.timer`.
- `smoke-test.sh:22` requires `ok=1` on the new poll row, not just a new row.
- `fsck --strict` exists (`cli.py:293-322`) and gates backup, preflight, smoke.
- O(archive) verify/fsck moved to daily `evidence-integrity.timer`; both it and
  `evidence-health.service` carry `Nice=10` / `IOSchedulingClass=idle`.
- `wait-for-resolvable.sh` no longer defaults to a UK host; the B2 pre-flight
  is gone from `evidence-backup.service`.
- `CONFIGURE.md:11` restores symlink-based reviewed configuration.
- `backup-archive.sh:188-191` copies `failed-anchor-attempts/` and
  `polls.db.pre-*`.
- `health-check.py` uses an anchored `<span id="generated">` regex, a
  cache-busting `evidence_health_nonce` query parameter, and a distinct
  oversized-response fault.
- `alert.sh` carries fault detail, escapes with `jq`, rate-limits on successful
  delivery only, and therefore retries after a failed one.
- `line()` in `smoke-test.sh` treats `NOT_RUN` as non-failing.
- `reconcile-fetch-semantics.py` is append-only, dry-run by default, idempotent.

---

## P0 — before the archive is carried to the new desktop

### 1. Put the archive somewhere other than this disk

69 of 164 polls and 20 of 31 anchor files exist on exactly one disk. Everything
else on this list is recoverable work; this is not.

Not blocked on a USB drive or on rclone. `evidence-archive-snapshot` is a
proven path used once already (`5f77911`, 3 Aug, verified by cloning back
byte-identical including `.ots` proofs, `.gitattributes` carrying `* -text
-diff`). Private repo, 3.5 MB; the archive is ~5.6 MB.

Git remains the wrong mechanism for *ongoing* backup — monotonic growth, no
pruning without a history rewrite — and B2 remains the plan. This is the
stopgap for machine loss, which is exactly the situation.

- Run `backup-archive.sh` to a local staging path; both source and copy must
  pass `verify` and `fsck --strict`.
- Commit the timestamped directory to `evidence-archive-snapshot`.
- Verify by cloning it back: `verify`, `fsck --strict`, `diff -r` against the
  source, and confirm `BACKUP.txt` heads recompute.
- Trust `git ls-remote`, not an exit code — the 3 Aug push returned 0 while the
  push had died. Use `http.postBuffer=200MB` if it stalls.

Acceptance: a clone of the snapshot repo passes verify, fsck --strict, and
`diff -r` byte-identical against `~/evidence-collection/archive`.

Unblocks items 1, 2 and 3 of the standing blocked list, including the gate in
front of task 9.

### 2. Fix the readiness guard — it stops the collector from starting

`evidence-collection.service:28`:

```
ExecStartPre=/usr/bin/env bash -c 'test -n "$EVIDENCE_READINESS_HOST" && exec ".../wait-for-resolvable.sh" "$EVIDENCE_READINESS_HOST"'
```

With `EVIDENCE_READINESS_HOST` unset or empty, `test -n` returns 1, `&&`
short-circuits, `bash -c` exits 1, `ExecStartPre` fails, and **the collector
does not start**. Verified by direct execution. This is the defect just removed
from the backup unit, now sitting in the one unit where failing closed means no
collection — and it contradicts the script's own header, which says an
unreachable target should be recorded as unreachable rather than not polled.

Second path, equally quiet: leaving `env.example:10`'s placeholder
(`replace-with-reviewed-host.example`) in place makes `getent` fail every cycle,
costing the full 120s wait on every start against `TimeoutStartSec=150`.

- Change to skip rather than fail: `[ -z "$EVIDENCE_READINESS_HOST" ] || exec ...`
  (or prefix the `ExecStartPre` line with `-`).
- Sweep every other `ExecStartPre` for the same `test -n ... &&` shape.
- Make `preflight.sh` fail if `EVIDENCE_READINESS_HOST` still matches the
  shipped placeholder.

Acceptance: a test unit with the variable unset starts; with the placeholder
set, preflight fails loudly rather than the collector waiting 120s.

---

## P1 — before the new host is enabled

### 3. Move the heartbeat off the control repo

`env.example:18` points `EVIDENCE_HEARTBEAT_URL` at
`api.github.com/repos/OWNER/evidence-control/dispatches`. `repository_dispatch`
needs a token with Contents write, so the collector would hold a credential that
can write to the control repo.

The control's whole evidential value is independence from the collector. A
collector that can write to it can forge the ticks that vouch for it.

- Point the heartbeat at a third repository. The dead-man workflow does not have
  to live where the control's history lives.
- Scope the token as narrowly as the API allows; keep it in
  `EVIDENCE_HEARTBEAT_TOKEN_FILE`, mode 600, never in the unit.

Acceptance: no credential on the collector host can write to
`petesherratt-collab/evidence-control`.

### 4. Land the dead-man switch

The heartbeat *sender* exists (`heartbeat.sh`, `evidence-heartbeat.*`). The
receiver does not — no workflow is present in this worktree. Until it alarms on
absence, the heartbeat posts into the void.

A local health timer cannot detect its own host being down, which is the failure
that has actually happened six times.

- Locate the prepared workflow, commit and push it to the repo chosen in task 3.
- Configure the absence threshold from the *observed* heartbeat cadence.
- Absence-test it: stop the heartbeat timer and confirm an alarm arrives.

Acceptance: stopping `evidence-heartbeat.timer` produces an off-host alarm
within the configured window.

### 5. Make alert delivery observable

`evidence-alert.service` has no `OnFailure=` and no positive-confirmation path;
`alert.sh` hard-requires `EVIDENCE_HEALTH_STATE` and `EVIDENCE_ALERT_STAMP` via
`${...:?}`. A dead webhook or a missing variable is discovered at the moment it
is needed.

- Route alert *failures* into the off-host watcher from task 4.
- Emit a periodic "alerting is alive" signal, or have the watcher treat a
  delivery-failure event as an alarm.
- Fix the suppression message: it says "previous attempt" when the stamp is only
  touched after a successful delivery.

### 6. Recalibrate the control thresholds against measured behaviour

`EVIDENCE_CONTROL_MAX_PUBLISHER_AGE_SECONDS=7200` was chosen against the
workflow's declared `*/15` cadence. Measured gaps across the last 8 ticks on
9 Aug: **27, 41, 43, 38, 46, 37, 33 minutes** — roughly half the declared rate.
Separately, the Pages deployment for the 20:19Z tick **failed** (run
`31333948905`), leaving the served page 59 minutes behind repo HEAD.

- Measure the real distribution over a longer window before fixing a threshold.
- Treat a failed Pages deploy as a distinct fault: repo HEAD and served content
  can diverge, and the collector currently cannot tell that from a dead
  collector.
- Correct `tick.yml`'s comment, which reasons from a 15-minute cadence the
  workflow does not achieve.

### 7. Drop `Persistent=true` from `evidence-heartbeat.timer`

It is meaningful on a calendar timer, but backwards for a heartbeat: after
downtime systemd fires a catch-up beat on boot. Nothing is backdated — the
payload uses `now` — but catching up a missed liveness signal is a contradiction.

---

## P2 — before the old host is decommissioned

### 8. Prevent two collectors mechanically

`preflight.sh`'s `pgrep` only sees the local host; `MIGRATE.md:12` is prose. The
new collector-instance annotations give attribution after the fact, not
prevention. Two divergent archives would each pass verify and fsck --strict.

- Add a startup check that refuses to run when the archive's latest instance
  annotation names a different instance, overridable only by an explicit
  documented handover step.

### 9. Apply the regime-3 correction

Dry run identifies exactly six affected annotations: **19, 20, 21, 22, 23, 25**.
Intentionally unapplied until task 1 is done.

- Run `reconcile-fetch-semantics.py --apply` only after the off-machine backup
  is verified.
- Re-run `verify` and `fsck --strict` afterwards; re-anchor.

### 10. Repository hygiene

- Decide whether agent itineraries belong in the evidence repository at all.
  `CODEX_KONSOLE_ITINERARY.md` is 373 lines of session scaffolding with absolute
  paths to a specific laptop, in a repo whose standing depends on reading as a
  disciplined public record. `deploy/` is better than the root; outside the repo
  may be better still.
- Commit the current worktree before starting any of the above; it is
  substantial and currently uncommitted.

---

## Standing test after every change

```sh
cd tools/kibitzr-archive && ../../.venv/bin/python -m pytest -q   # expect 220+
cd ~/evidence-collection && .venv/bin/kibitzr archive verify
cd ~/evidence-collection && .venv/bin/kibitzr archive fsck --strict
systemd-analyze verify repo/deploy/*.service repo/deploy/*.timer
```

Confirm at the end of every session that this laptop has no enabled or running
collector, anchor, backup, health, integrity or heartbeat units.
