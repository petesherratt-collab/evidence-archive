# Configure a collector

1. Copy `deploy/evidence-collection.env.example` to
   `~/.config/evidence-archive/environment`, replace every placeholder with an
   absolute path (EnvironmentFile does not expand `~`, `$HOME` or `%h`), and
   restrict it to mode 600.
2. Copy a reviewed target pack to `$EVIDENCE_ROOT/kibitzr.yml`. UK procurement
   is a reference under `examples/uk-procurement`; it is not an engine default.
3. Set `EVIDENCE_CONTROL_URL` to the independently operated control target.
4. Create `$EVIDENCE_ARCHIVE` as an empty, owner-only directory. Running the
   first poll creates its database and blob layout.
5. Set `EVIDENCE_BACKUP_DEST` to a writable local path or an rclone destination.
   Configure rclone interactively with a least-privilege, non-delete credential.

Application configuration is the reviewed target YAML: URLs, selectors,
transforms and periods. Host configuration is paths, backup destination,
credentials, control URL and system service policy. Do not commit host config,
rclone files, tokens, keys, filled private watchlists, or archive data.
