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
./.venv/bin/kibitzr archive status    # polls, raw changes, doc changes, last seen
./.venv/bin/kibitzr archive verify    # recompute chains; non-zero if broken
./.venv/bin/kibitzr archive head      # the value to submit for timestamping
journalctl --user -u evidence-collection -f
```

Read `doc chg`, not `raw chg`. Three of the five targets churn at the raw level
on every request (CSP nonces) while their tuned selector sits still.

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
