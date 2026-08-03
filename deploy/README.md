# Running a collection host

Collection began **1 Aug 2026** on a laptop, with the intention of migrating to
an always-on host once the run proves out. Everything here is written so that
migration is a copy rather than a rewrite.

## Layout

The run root is separate from the checkout, because the archive is data and the
repo is code:

```
evidence-collection/
  repo/            clone of this repository
  .venv/           kibitzr + the archive plugin
  kibitzr.yml      symlink -> repo/trials/gov-contracts/kibitzr.yml
  archive/         polls.db and blobs/   <- the actual evidence, not in git
```

Keeping `kibitzr.yml` a symlink means the running config and the reviewed config
cannot drift.

## Install

```sh
ROOT=~/evidence-collection
mkdir -p $ROOT && cd $ROOT
git clone https://github.com/petesherratt-collab/evidence-archive.git repo
python3 -m venv .venv
./.venv/bin/pip install kibitzr
./.venv/bin/pip install -e ./repo/tools/kibitzr-archive
ln -sf repo/trials/gov-contracts/kibitzr.yml kibitzr.yml
sudo apt install jq          # the OCDS checks reduce through jq
```

Then:

```sh
cp repo/deploy/evidence-collection.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now evidence-collection.service
loginctl enable-linger "$USER"     # survive logout — easy to forget
```

`kibitzr run` is a long-lived process that keeps its own schedule from each
check's `period`. Do not replace it with a systemd timer firing `kibitzr once`:
that would ignore the per-check periods and poll every target at the same rate.

## Checking on it

```sh
./.venv/bin/kibitzr archive status       # polls, raw changes, doc changes, last seen
./.venv/bin/kibitzr archive verify       # recompute chains; non-zero if broken
./.venv/bin/kibitzr archive fsck         # blobs and proofs, which verify cannot see
./.venv/bin/kibitzr archive head         # the value to submit for timestamping
./.venv/bin/kibitzr archive gaps         # holes, read against declared schedule
./.venv/bin/kibitzr archive annotations  # corrections and regime changes
journalctl --user -u evidence-collection -f
```

Run these **from the run root**: the CLI resolves `archive/` relative to the
working directory and fails confusingly from anywhere else.

Read `doc chg`, not `raw chg`. Three of the five targets churn at the raw level
on every request (CSP nonces) while their tuned selector sits still.

## Anchoring

Anchoring runs daily from **2 Aug 2026**, on its own timers, against its own
virtualenv so it can never disturb collection:

```sh
python3 -m venv .venv-anchor
./.venv-anchor/bin/pip install opentimestamps-client
cp repo/deploy/evidence-anchor*.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now evidence-anchor.timer evidence-anchor-upgrade.timer
```

`evidence-anchor.timer` stamps the chain heads daily; `evidence-anchor-upgrade`
runs twice daily to convert calendar attestations into Bitcoin ones once a block
has confirmed. Both are `Persistent=true`, because this laptop is not always on
and a skipped day is the one loss anchoring cannot make good later.

It was started before the schema had settled, deliberately. Proofs accumulate: an
anchor over today's heads proves those heads existed today, permanently, whatever
the format does afterwards, and a later anchor adds a proof rather than replacing
one. Waiting would only have traded a permanent gain for tidiness.

Check exposure with `./.venv/bin/kibitzr archive anchors` — the polls-not-yet-
covered count is what has no external evidence of when it existed.

Third-party verification: `deploy/VERIFYING.md`, with a standard-library
implementation in `deploy/verify_independently.py` that shares no code with the
plugin.

## Boot ordering

The unit deliberately has no `After=network-online.target`. It carried one
until 2 Aug 2026, with a comment claiming it kept boot-time DNS failures out of
the archive — and it never worked: `network-online.target` exists in the system
systemd manager, not in the per-user manager this service runs under, where it
is simply not-found and the ordering is ignored. The service started ahead of
the network after a reboot and wrote 25 failed polls, each recording the wrong
cause.

A user unit cannot order against a system target, so `ExecStartPre` runs
`deploy/wait-for-resolvable.sh`, which waits for DNS with a bounded deadline and
then starts anyway. Starting anyway is deliberate: a genuinely unreachable
target should be recorded as unreachable, which is a true observation, and a
poll not attempted is worth less than a poll that honestly failed.

If you port this unit to a *system* service, the wait becomes redundant and
`After=network-online.target` starts working — but leave the `ExecStartPre` in
unless you have checked that the target is actually pulled in.

## Backing it up

The archive is the only part of this project that exists in one place. The
repository is on GitHub and the collector is a hundred lines of config; the
poll log, the retained responses and the proofs are not recoverable from
anywhere if this disk fails.

```sh
./repo/deploy/backup-archive.sh b2:BUCKET/laptop      # off-site, unattended
./repo/deploy/backup-archive.sh /media/peters/DRIVE   # removable media
```

It checks the source, snapshots `polls.db`, copies `blobs/` and `anchors/`,
flushes to the device, then runs `verify` **and** `fsck` against the copy. The
copy is assembled under `.incomplete-<stamp>` and only renamed to
`evidence-archive-<stamp>` once both pass, so an interrupted run leaves
something named for what it is rather than something mistakable for a backup.

### Off-site, nightly

A removable drive needs a human present, so it will always lag. At 4 MB there
is no reason to have one in the loop. What usually destroys an archive is not
disk failure but loss of the machine — theft, fire, flood, or simply replacing
the laptop — and only an off-site copy addresses that.

Create a **private** B2 bucket, then an application key **restricted to that
bucket** with `listBuckets`, `listFiles`, `readFiles`, `writeFiles` — and
**not** `deleteFiles`. The script only ever uploads a new timestamped
directory and never removes anything, so write-and-list is sufficient, and it
means a compromised laptop cannot destroy the backups it made. That is the
same append-only shape as everything else here.

```sh
curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip
unzip -j /tmp/rclone.zip '*/rclone' -d ~/.local/bin && chmod +x ~/.local/bin/rclone

~/.local/bin/rclone config create b2 b2 \
    account YOUR_KEY_ID key YOUR_APPLICATION_KEY

cp repo/deploy/evidence-backup.{service,timer} ~/.config/systemd/user/
# set the bucket in evidence-backup.service, then:
systemctl --user daemon-reload
systemctl --user enable --now evidence-backup.timer
systemctl --user start evidence-backup.service     # don't wait for 02:40
```

Runs at 02:40, `Persistent=true` so a night the laptop was asleep is caught at
next boot rather than silently skipped. That sits after the anchor timer
(00:20) and before the upgrade (06:34), so each night's copy contains that
night's proof instead of trailing it by a day.

**The remote does not have to be trusted.** `rclone check` compares hashes, so
transfer corruption is caught on the way in; anything subtler — including a
`polls.db` swapped for an older one — is caught by `fsck` on restore, via the
anchor-to-`poll_head` cross-check. That is precisely what the chains and
anchors were built for, so cheap untrusted storage is fine here in a way it
would not be for most backups.

The remote cannot use the rename trick, because a key without delete
permission cannot rename (a rename is a copy plus a delete). A
`BACKUP-COMPLETE.txt` written only after `rclone check` passes stands in for
it: a remote directory lacking that file is a known-incomplete upload.

### Restoring

```sh
~/.local/bin/rclone copy b2:BUCKET/laptop/evidence-archive-<stamp> ./restored
kibitzr archive verify --root ./restored
kibitzr archive fsck   --root ./restored
```

Both must pass before the copy is trusted. Nothing about a backup is
established by its existence.

Two things it is built against, both of which otherwise produce a copy that
looks fine:

- **`cp polls.db` while the collector is running.** Journal mode is `delete`,
  not WAL, so a copy taken mid-transaction captures a torn database whose
  rollback journal is a separate file the copy did not include. The script uses
  SQLite's `VACUUM INTO`, which takes a read lock and writes a consistent
  snapshot — collection does not need to stop. The resulting file is
  defragmented and byte-different from the original; what is preserved is the
  rows and their chains, not the file image.
- **A copy that fills the drive or is unplugged part-way.** `verify` cannot see
  this: an archive holding a perfect log and zero blobs reports every chain
  intact. That is what `fsck` is for. See "What the chains do not cover" in the
  plugin README.

A copy of a Python environment is not a backup of anything. If a drive holds
`site-packages/`, `twilio/` or `.pyc` files it is a venv copy and worth
nothing; what matters is `polls.db`, `blobs/` and `anchors/`.

## Migrating to an always-on host

Rebuild with the steps above, then move `archive/` across intact — the poll and
normalisation chains are computed over the rows, so the archive verifies on the
new host without rebuilding. Run `archive verify` **and `archive fsck`** on both
ends of the copy: verify alone would pass on a transfer that dropped every
retained response.

Stop the old host **before** copying, and do not run both: kibitzr does not
coordinate between hosts, and two collectors writing separate archives for the
same checks produce two chains that each look intact while neither is complete.

## What a gap looks like

A laptop that sleeps stops polling, and the log will show it as elapsed time
between polls rather than as a recorded failure. That is the honest reading:
the archive can say what it saw and when, and it can say that it saw nothing in
between — but a gap it never attempted is not the same as a poll that failed,
and only the second appears in `polls.db`.

Since 2 Aug 2026 that elapsed time is also *interpretable*. Each check records
its intended period as a `schedule` annotation, so `archive gaps` can judge a
hole against the intent in force when it opened, rather than leaving a reader to
guess whether we meant to be watching. The overnight hole of 1–2 Aug is the
worked example: 13.9h against a declared 6h period.

Declaring the schedule does not prove the process was up. It narrows the
ambiguity from "were we even trying?" to "we were trying and missed", which is
the part the archive can honestly speak to.
