# Fresh collector bootstrap

Perform in order and stop on any failure:

1. Follow `INSTALL.md`; checkout a reviewed immutable tag or commit.
2. Follow `CONFIGURE.md`; create the empty archive and install target config.
3. Run `deploy/preflight.sh` and resolve every failure.
4. Run one deliberate control poll with `kibitzr once`; inspect the retained
   response and transform result. This mutates the new archive by design.
5. Run `kibitzr archive verify --root "$EVIDENCE_ARCHIVE"` and `archive fsck`.
6. Run `deploy/smoke-test.sh --live`; connectivity alone is not evidence.
7. Copy `deploy/*.service` and `deploy/*.timer` to
   `~/.config/systemd/user/`; run `systemd-analyze verify` and
   `systemctl --user daemon-reload`.
   Run `loginctl enable-linger "$USER"` and verify `Linger=yes` before relying
   on user units without an interactive login.
8. Start the collector without enabling it; inspect journal and archive growth.
9. Start one anchor and verify a proof exists (pending is expected initially).
10. Run one backup, restore it elsewhere, and independently verify/fsck it.
11. Send a test alert, then enable collector, anchor, upgrade, backup and health
    mechanisms. Confirm a deliberately failed health check produces an alert.
12. Re-run preflight, inspect first real polls, and record version, host and
    regime transition as an append-only annotation where appropriate.
