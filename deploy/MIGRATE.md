# Migrate an existing authoritative archive

1. Stop the old collector and disable it and all production timers.
2. Confirm no `kibitzr run` or `kibitzr once` process remains.
3. Run verify and fsck on the source; record `kibitzr archive head` output.
4. Take a verified backup with `backup-archive.sh`; preserve its completion
   marker and record the selected repository commit and package versions.
5. Transfer that verified backup to the destination without rewriting files.
6. Run verify, fsck and the independent verifier on the destination.
7. Compare destination chain heads byte-for-byte with those recorded at source.
8. Configure the new host and run preflight. Ensure the old host cannot restart
   through enablement, lingering sessions, cron, containers or supervisor jobs.
9. Start exactly one new collector. If the annotation command/policy is
   available, append a factual host/regime transition without changing history.
10. Verify and inspect the first new polls, then anchor and back up again.

Never overlap collectors. A valid chain on each of two divergent archives does
not establish a complete authoritative history.

