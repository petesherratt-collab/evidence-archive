# Deployment and operations

This checkout prepares a separate, continuously powered Linux desktop to be the
authoritative collector. A development machine may run bounded tests but must
not retain enabled production services or become a second authority.

Use these documents in order:

- `INSTALL.md` — supported host, exact environment creation and software pins.
- `CONFIGURE.md` — target config versus host-private config and credentials.
- `BOOTSTRAP.md` — ordered bring-up and acceptance procedure.
- `MIGRATE.md` — evidence-preserving transfer with no collector overlap.
- `PRODUCTION_CHECKLIST.md` — final operator checklist.
- `VERIFYING.md` — independent integrity verification and its limits.

Run `./deploy/preflight.sh` before deployment. It is read-only and fails if a
required dependency, host decision, network/control/OTS/backup check, systemd
validation, or existing archive verification fails. Run
`./deploy/smoke-test.sh --live` only against the deliberately selected temporary
or production archive: `--live` performs one poll and a verified backup, so it
appends archive data and writes to the configured backup destination.

The archive (`polls.db`, `blobs/`, `anchors/`) is data, not source code. Never
copy a virtualenv as a deployment, never run two collectors for one authority,
and never interpret connectivity or an empty result as an evidentiary guarantee.

`backup-archive.sh DEST [RUN_ROOT]` makes a SQLite-consistent snapshot, copies
blobs and proofs, and runs both verify and fsck before declaring completion.
For rclone, use a private destination and a credential scoped to list, read and
write without delete permission. Restore into a separate directory and verify
independently before trusting it.

The supplied systemd user units read
`~/.config/evidence-archive/environment`. Installation is intentionally a
separate operator action; repository tests must never enable them.
