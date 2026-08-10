# Configure a collector

1. Copy `deploy/evidence-collection.env.example` to
   `~/.config/evidence-archive/environment`, replace every placeholder with an
   absolute path (EnvironmentFile does not expand `~`, `$HOME` or `%h`), and
   restrict it to mode 600.
2. Symlink the reviewed target pack to `$EVIDENCE_ROOT/kibitzr.yml`, so running
   configuration cannot drift from the selected repository revision. UK
   procurement is a reference under `examples/uk-procurement`; it is not an
   engine default. Keep host-private filled watchlists outside a public clone,
   but still symlink rather than making an untracked operational copy.
3. Set `EVIDENCE_CONTROL_URL` to the independently operated control target.
4. Create `$EVIDENCE_ARCHIVE` as an empty, owner-only directory. Running the
   first poll creates its database and blob layout.
5. Set `EVIDENCE_BACKUP_DEST` to a writable local path or an rclone destination.
   Configure rclone interactively with a least-privilege, non-delete credential.
6. Set `BACKUP_STAGE_DIR` to a writable persistent filesystem with room for at
   least 120% of the archive. Remote backups fail closed without it.
7. Set `EVIDENCE_ALERT_URL` to the host-private HTTPS webhook that receives
   health failures; do not commit its credential-bearing URL.
8. Configure `EVIDENCE_HEARTBEAT_URL` and a mode-600 token file for the
   off-host dead-man workflow. The token needs only permission to dispatch the
   `evidence-control` workflow. Local health alerts and off-host heartbeat
   absence cover different failures; production needs both.
9. Set publisher and poll freshness thresholds from measured behavior, not the
   requested GitHub Actions cron. The example permits four hours of publisher
   age because the control history has exhibited one-to-three-hour gaps; it is
   a fault-classification threshold, not a claim that the cadence is healthy.

Application configuration is the reviewed target YAML: URLs, selectors,
transforms and periods. Host configuration is paths, backup destination,
credentials, control URL and system service policy. Do not commit host config,
rclone files, tokens, keys, filled private watchlists, or archive data.
