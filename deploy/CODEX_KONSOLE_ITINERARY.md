# Codex Konsole itinerary — evidence collector production readiness

Use this file as the complete resume brief for a new Codex session opened in
Konsole. Start in:

```sh
cd /home/peters/evidence-collection/repo
```

## Mission

Prepare this repository so a separate, continuously powered Linux desktop can
become the authoritative evidence collector through a boring, reproducible,
fail-closed installation. This Kubuntu laptop is development and integration
infrastructure only. Do not leave production collection or timers enabled here.

Success is not “the daemon runs.” Success is a rehearsed clean-machine process
that does not depend on undocumented state from this laptop.

## Non-negotiable safety boundaries

- Never run two collectors against the same authoritative archive.
- Do not mutate historical evidence or replace an existing snapshot.
- Run both archive `verify` and `fsck`; neither substitutes for the other.
- Pending OpenTimestamps proofs are not completed Bitcoin attestations.
- Do not invent backup destinations, alert endpoints, credentials or secrets.
- Do not publish raw responses, deploy, merge, tag, push or mark a PR ready
  without explicit approval.
- Preserve unrelated dirty-worktree changes in every repository.
- At the end, explicitly confirm that this laptop has no enabled/running
  collector, anchor, backup or health production services.

## Repository state at handoff

Primary repository:

```text
/home/peters/evidence-collection/repo
branch: codex/evidence-change-browser
base checkpoint: 6f21023 Prepare evidence archive for portable Linux deployment
```

There are uncommitted deployment-hardening changes after `6f21023`. Inspect
them before editing:

```sh
git status -sb
git diff --check
git diff
```

Control repository:

```text
/home/peters/evidence-control
branch: main
```

It also contains uncommitted changes. `README.md` was already dirty before the
latest control edits, so preserve and separate pre-existing user work. Nothing
has been committed or pushed in either repository.

Snapshot repository:

```text
/home/peters/evidence-archive-snapshot
latest snapshot commit: 5f77911 (3 August 2026)
```

The snapshot checkout already has unrelated modifications to `.gitattributes`
and `README.md`. Do not overwrite or discard them.

## Adversarial findings and disposition

### P0 — off-machine evidence gap: OPEN and blocking

- The local archive has progressed beyond the 3 August snapshot.
- No usable rclone configuration currently exists at the default location.
- `rclone lsf b2:` fails because there is no `b2` section.
- No new off-machine copy was written during the previous session.

Required next action needs operator authority and credentials. Choose one:

1. Configure a least-privilege B2/rclone destination and make a fresh verified
   backup, or
2. Create a new immutable snapshot in the private snapshot repository without
   replacing the 3 August snapshot or losing its dirty user changes.

For either route: verify/fsck source, record chain heads, copy, restore into a
separate directory, verify/fsck destination, compare heads, and preserve a
completion marker. Do not report success merely because an upload exists.

### P0 — backup staging fallback: FIXED locally

`deploy/backup-archive.sh` now refuses a remote backup unless
`BACKUP_STAGE_DIR` is explicitly set. It no longer falls back to `/tmp`.
Preflight checks that the directory exists, is writable and is not tmpfs/ramfs.

### P0 — health mechanism: FIXED locally, not installed

Added:

- `deploy/health-check.py`
- `deploy/health-check.sh`
- `deploy/evidence-health.service`
- `deploy/evidence-health.timer`
- `deploy/alert.sh`
- `deploy/evidence-alert.service`

Health now runs verify/fsck and distinguishes:

- control publisher unreachable
- control publisher stale
- collector poll stale
- collector behind a fresh publisher
- unreadable control evidence
- missing normalisation for the latest control poll

Alert delivery uses a host-private `EVIDENCE_ALERT_URL`. It has not been
configured or tested because no destination was supplied.

### P0 — user-service linger: FIXED in tooling/docs

`deploy/INSTALL.md` now requires:

```sh
loginctl enable-linger "$USER"
test "$(loginctl show-user "$USER" -p Linger --value)" = yes
```

Preflight checks `Linger=yes`. The current laptop reports linger enabled, but
that does not replace testing it on the future production host.

### P0 — smoke-test theatre: FIXED locally

`deploy/smoke-test.sh` now:

- prints `NOT_RUN` and exits non-zero when live checks are omitted
- proves a live control poll appended exactly one poll row
- proves that poll has a linked normalisation row
- runs verify and fsck
- reports actual OTS complete/pending state
- requires the backup command to complete
- invokes real health semantics
- sends an actual test alert in live mode

Do not run `--live` casually: it appends to the selected archive, writes a
backup and sends an alert.

### P0/P1 — control reliability: PARTIALLY FIXED locally

The control is a best-effort GitHub Actions publisher. Its requested 15-minute
schedule is not guaranteed to be faster than the hourly collector.

Local changes in `/home/peters/evidence-control`:

- control documentation no longer promises every poll sees a new tick
- publisher freshness and collector freshness are treated separately
- `build.py` now uses forthcoming history count (`HEAD` count plus one)
- generated page wording no longer treats a stale control as proof of one
  specific failure

The design still relies on GitHub Actions and GitHub Pages. Do not claim a
guaranteed cadence. A stronger future design could split publisher scheduling
and hosting across independent systems, but do not build it without approval.

### P1 — Python support: FIXED in docs

`deploy/INSTALL.md` now says the tested production baseline is Python 3.13.
Do not claim Python 3.9–3.12 deployment support until rehearsed there.

### P1 — origin-fetch wording: FIXED locally

Core notes and plugin documentation now state that cache removal guarantees a
network fetch, not origin-server contact. A CDN or reverse proxy may still
serve the representation.

## Current verification evidence

Latest completed checks:

```text
full pytest suite:             217 passed in 107.41s
focused deployment/control:    23 passed
bash syntax:                   passed
Python compilation:            passed
git diff --check:              passed in both repositories
systemd-analyze verify:        passed
archive verify:                passed
archive fsck:                  passed
clock/NTP:                     passed
linger:                        yes
collector process check:       none running
```

The hardened preflight intentionally exits non-zero on this laptop because:

```text
EVIDENCE_BACKUP_DEST unset
BACKUP_STAGE_DIR absent
EVIDENCE_ALERT_URL unset
```

All other checked items passed, including live control health at the time of
the last run. A health pass is time-sensitive; rerun it rather than quoting it
as current.

## Laptop service state at handoff

Last verified state:

```text
evidence-collection.service:    disabled, not running (failed state retained)
evidence-anchor.timer:          disabled, inactive
evidence-anchor-upgrade.timer:  disabled, inactive
evidence-backup.timer:          not found
evidence-health.timer:          not installed
kibitzr run/once processes:     none
```

Re-check before and after every live test:

```sh
systemctl --user is-enabled \
  evidence-collection.service \
  evidence-anchor.timer \
  evidence-anchor-upgrade.timer \
  evidence-backup.timer \
  evidence-health.timer

systemctl --user is-active \
  evidence-collection.service \
  evidence-anchor.timer \
  evidence-anchor-upgrade.timer \
  evidence-backup.timer \
  evidence-health.timer

pgrep -af 'kibitzr (run|once)' || true
```

If testing starts anything, stop and disable it before finishing.

## Resume itinerary

### 1. Establish exact state

```sh
cd /home/peters/evidence-collection/repo
git status -sb
git diff --check
git diff

git -C /home/peters/evidence-control status -sb
git -C /home/peters/evidence-control diff --check
git -C /home/peters/evidence-control diff

git -C /home/peters/evidence-archive-snapshot status -sb
```

Do not clean, reset, checkout over or stash user changes without approval.

### 2. Re-run local validation

```sh
cd /home/peters/evidence-collection/repo
bash -n deploy/*.sh
systemd-analyze verify deploy/*.service deploy/*.timer
../.venv/bin/python -m pytest tools/kibitzr-archive/tests -q
git diff --check
```

Tests use localhost fixtures; a restricted sandbox may need loopback approval.

### 3. Run preflight read-only

Create a temporary host environment file outside Git. Required fields are in
`deploy/evidence-collection.env.example`. Then:

```sh
EVIDENCE_ENV_FILE=/path/to/temporary/environment ./deploy/preflight.sh
```

Do not weaken failures to make preflight green. Missing backup, staging or
alert configuration is a real blocker.

### 4. Close the off-machine P0

Stop and request operator direction if no backup route is explicitly chosen.
Never inspect or print secret values. It is safe to inspect remote names and
credential-file permissions, but remote writes require clear authorization.

Before copying:

```sh
/home/peters/evidence-collection/.venv/bin/kibitzr archive verify \
  --root /home/peters/evidence-collection/archive
/home/peters/evidence-collection/.venv/bin/kibitzr archive fsck \
  --root /home/peters/evidence-collection/archive
/home/peters/evidence-collection/.venv/bin/kibitzr archive head \
  --root /home/peters/evidence-collection/archive
```

After copying, restore to a different directory, repeat verify/fsck, run
`deploy/verify_independently.py`, and compare recorded heads exactly.

### 5. Finish review without overclaiming

Reassess:

- health thresholds against observed publisher and poll cadence
- alert payload compatibility with the chosen destination
- backup staging capacity against archive growth
- whether health should require a recent successful restored-backup drill
- whether the control needs an independent publisher/host before production
- systemd behavior across logout and reboot on a disposable host

### 6. Clean-machine rehearsal

No Docker, Podman or Distrobox was available during the previous pass. When a
disposable Linux environment is available, start from documented prerequisites
and exercise `INSTALL.md`, `CONFIGURE.md`, `BOOTSTRAP.md`, preflight, one safe
control poll, verify/fsck, health, alert and verified backup restore.

Do not call the repository production-ready until this rehearsal and the
off-machine restore drill pass.

### 7. Handoff requirements

Report all of the following, including failures and unrun checks:

- tests and exact results
- preflight result
- clean-install rehearsal result
- exact package versions
- backup destination and restore-verification evidence
- remaining host decisions and files to edit
- required credentials without exposing their values
- expected disk and network requirements
- final production installation commands
- exact collector/timer state on this laptop

## Important files

```text
deploy/INSTALL.md
deploy/CONFIGURE.md
deploy/BOOTSTRAP.md
deploy/MIGRATE.md
deploy/PRODUCTION_CHECKLIST.md
deploy/preflight.sh
deploy/smoke-test.sh
deploy/health-check.py
deploy/health-check.sh
deploy/alert.sh
deploy/backup-archive.sh
deploy/evidence-collection.env.example
requirements-deploy.txt
requirements-deploy.lock
tools/kibitzr-archive/kibitzr_archive/store.py
tools/kibitzr-archive/tests/test_deployment.py
```

## First message for the resumed Codex session

Paste this after opening Codex in Konsole:

```text
Read CODEX_KONSOLE_ITINERARY.md completely and follow it as the authoritative
resume brief. Begin with read-only repository, service and archive checks.
Preserve all existing dirty-worktree changes. Do not enable production services
or write off-machine until the destination and authority are explicit. Then
continue closing the open P0 backup/restore gap and validate every accepted fix.
```
