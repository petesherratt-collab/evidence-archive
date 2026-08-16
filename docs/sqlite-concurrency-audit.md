# SQLite concurrency audit

The archive currently uses SQLite's `delete` journal mode. No code in this
branch changes it. `ArchiveStore._connect()` and the read-only exporter and
verification utilities use `sqlite3.connect()` without an explicit timeout,
so CPython's default five-second connection timeout applies. On the current
archive `PRAGMA busy_timeout` reports `5000` milliseconds, which is the SQLite
form of that same setting; adding `PRAGMA busy_timeout = 5000` would therefore
duplicate existing effective behaviour rather than materially improve it.

Collector writes open short per-operation connections. Reports, public export,
integrity verification, health/audit scripts, backup staging, and the new public
export verifier can overlap those writes. The public exporter and new verifier
open the database read-only; other independent audit tools use ordinary
connections but do not issue writes. Repository tests and inspected project
logs contain no `database is locked` failure evidence.

Recommendation: keep the present five-second timeout until observed contention
shows it is inadequate. If a future change wants to make the timeout explicit,
do so consistently for every connection and add a writer/long-reader lock test.
Do not enable WAL incidentally. WAL could improve reader/writer overlap, but it
changes backup completeness (`-wal`/`-shm` files), checkpointing, recovery, and
quiescence assumptions; it deserves a dedicated operational design and restore
test before adoption.
