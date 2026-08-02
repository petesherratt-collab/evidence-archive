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
./.venv/bin/kibitzr archive head         # the value to submit for timestamping
./.venv/bin/kibitzr archive gaps         # holes, read against declared schedule
./.venv/bin/kibitzr archive annotations  # corrections and regime changes
journalctl --user -u evidence-collection -f
```

Run these **from the run root**: the CLI resolves `archive/` relative to the
working directory and fails confusingly from anywhere else.

Read `doc chg`, not `raw chg`. Three of the five targets churn at the raw level
on every request (CSP nonces) while their tuned selector sits still.

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

## Migrating to an always-on host

Rebuild with the steps above, then move `archive/` across intact — the poll and
normalisation chains are computed over the rows, so the archive verifies on the
new host without rebuilding. Run `archive verify` on both ends of the copy.

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
