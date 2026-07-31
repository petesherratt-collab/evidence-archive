"""Append-only poll log and content-addressed raw-response store.

Two things kibitzr does not do, and which an evidential archive needs:

1. A record of *every* poll, not just the ones that changed. Without it,
   silence in the archive cannot distinguish "we checked and it was the
   same" from "we were not watching".
2. Retention of the fetched response before the transform chain touches
   it, so a third party can re-derive the extraction rather than taking
   the extracted text on trust.

Poll rows are chained: each row's ``record_hash`` covers the row's
identifying fields plus the previous row's ``record_hash``. Anchoring the
latest ``record_hash`` for a check therefore anchors its whole history,
which is the seam an external timestamp proof attaches to.

This module deliberately has no kibitzr imports so it can be tested and
reused standalone.
"""
import gzip
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone


SCHEMA_VERSION = 1
GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS poll (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name      TEXT    NOT NULL,
    url             TEXT,
    polled_at       TEXT    NOT NULL,
    ok              INTEGER NOT NULL,
    http_status     INTEGER,
    content_length  INTEGER,
    content_sha256  TEXT,
    etag            TEXT,
    last_modified   TEXT,
    changed         INTEGER NOT NULL,
    raw_ref         TEXT,
    error           TEXT,
    prev_hash       TEXT    NOT NULL,
    record_hash     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS poll_check_idx ON poll (check_name, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def sha256_hex(data):
    """Return hex SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def utc_now_iso():
    """Return current UTC time as a second-precision ISO 8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_record_hash(fields, prev_hash):
    """Hash a poll's identifying fields chained onto the previous record.

    Serialisation is canonical (sorted keys, no whitespace) so the value
    is reproducible by anyone holding the same row.
    """
    payload = dict(fields, v=SCHEMA_VERSION, prev=prev_hash)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return sha256_hex(encoded.encode('utf-8'))


class PollRecord:
    """Outcome of recording one poll."""

    __slots__ = ('changed', 'content_sha256', 'raw_ref', 'record_hash', 'polled_at')

    def __init__(self, changed, content_sha256, raw_ref, record_hash, polled_at):
        self.changed = changed
        self.content_sha256 = content_sha256
        self.raw_ref = raw_ref
        self.record_hash = record_hash
        self.polled_at = polled_at

    def __repr__(self):
        return (f"PollRecord(changed={self.changed}, "
                f"content_sha256={self.content_sha256!r}, "
                f"record_hash={self.record_hash!r})")


class ArchiveStore:
    """Poll log (SQLite) plus write-once content-addressed blob store."""

    DB_NAME = "polls.db"
    BLOB_DIR = "blobs"

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.blob_root = os.path.join(self.root, self.BLOB_DIR)
        os.makedirs(self.blob_root, exist_ok=True)
        self.db_path = os.path.join(self.root, self.DB_NAME)
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    # -- blob store ------------------------------------------------------

    def blob_path(self, digest):
        """Return the on-disk path for a blob digest."""
        return os.path.join(self.blob_root, digest[:2], digest + ".gz")

    def has_blob(self, digest):
        return os.path.exists(self.blob_path(digest))

    def put_blob(self, data):
        """Store bytes under their digest. Write-once; returns the digest.

        Content addressing makes this idempotent: re-observing content we
        already hold costs nothing, so retention is cheap even when a
        document flaps between two states.
        """
        digest = sha256_hex(data)
        path = self.blob_path(digest)
        if os.path.exists(path):
            return digest
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wb") as fp:
            fp.write(data)
        os.replace(tmp, path)
        return digest

    def get_blob(self, digest):
        """Return the stored bytes for a digest."""
        with gzip.open(self.blob_path(digest), "rb") as fp:
            return fp.read()

    # -- poll log --------------------------------------------------------

    def last_poll(self, check_name):
        """Return the most recent poll row for a check, or None."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM poll WHERE check_name = ? ORDER BY id DESC LIMIT 1",
                (check_name,),
            )
            return cur.fetchone()

    def _last_observed_hash(self, conn, check_name):
        """Content hash of the last poll that actually observed content.

        Failed fetches must not read as a change, so they are skipped when
        deciding whether content moved.
        """
        cur = conn.execute(
            "SELECT content_sha256 FROM poll "
            "WHERE check_name = ? AND content_sha256 IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (check_name,),
        )
        row = cur.fetchone()
        return row["content_sha256"] if row else None

    def record_poll(self, check_name, url=None, ok=True, content=None,
                    http_status=None, etag=None, last_modified=None,
                    error=None, polled_at=None):
        """Append one poll to the log, retaining content if it is new.

        ``content`` is the response as fetched, before any transform. Pass
        None for a failed fetch; the poll is still logged, with ``changed``
        false, so the gap is visible as an attempted observation.
        """
        polled_at = polled_at or utc_now_iso()
        raw = None
        if content is not None:
            raw = content.encode("utf-8") if isinstance(content, str) else content

        digest = sha256_hex(raw) if raw is not None else None
        raw_ref = None

        with self._connect() as conn:
            previous = self._last_observed_hash(conn, check_name)
            changed = bool(digest is not None and digest != previous)

            if raw is not None and not self.has_blob(digest):
                raw_ref = self.put_blob(raw)
            elif raw is not None:
                raw_ref = digest

            last = conn.execute(
                "SELECT record_hash FROM poll WHERE check_name = ? "
                "ORDER BY id DESC LIMIT 1",
                (check_name,),
            ).fetchone()
            prev_hash = last["record_hash"] if last else GENESIS

            fields = {
                "check": check_name,
                "url": url,
                "polled_at": polled_at,
                "ok": bool(ok),
                "http_status": http_status,
                "content_sha256": digest,
                "changed": changed,
            }
            record_hash = compute_record_hash(fields, prev_hash)

            conn.execute(
                "INSERT INTO poll (check_name, url, polled_at, ok, http_status,"
                " content_length, content_sha256, etag, last_modified, changed,"
                " raw_ref, error, prev_hash, record_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (check_name, url, polled_at, int(bool(ok)), http_status,
                 len(raw) if raw is not None else None, digest, etag,
                 last_modified, int(changed), raw_ref, error, prev_hash,
                 record_hash),
            )

        return PollRecord(changed, digest, raw_ref, record_hash, polled_at)

    # -- integrity -------------------------------------------------------

    def head(self, check_name):
        """Return the current chain head for a check, or None.

        This is the value to submit for external timestamping: anchoring it
        anchors every poll recorded up to that point.
        """
        row = self.last_poll(check_name)
        return row["record_hash"] if row else None

    def verify_chain(self, check_name):
        """Recompute the hash chain for a check.

        Returns (True, None) if intact, else (False, id_of_first_bad_row).
        Detects edits to logged fields and removal of rows, which is the
        point: the log has to be able to incriminate its own keeper.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM poll WHERE check_name = ? ORDER BY id",
                (check_name,),
            ).fetchall()

        prev_hash = GENESIS
        for row in rows:
            if row["prev_hash"] != prev_hash:
                return False, row["id"]
            fields = {
                "check": row["check_name"],
                "url": row["url"],
                "polled_at": row["polled_at"],
                "ok": bool(row["ok"]),
                "http_status": row["http_status"],
                "content_sha256": row["content_sha256"],
                "changed": bool(row["changed"]),
            }
            if compute_record_hash(fields, prev_hash) != row["record_hash"]:
                return False, row["id"]
            prev_hash = row["record_hash"]
        return True, None

    def stats(self, check_name=None):
        """Return (polls, changes, blobs, bytes_on_disk) for reporting.

        Growth rate is the cheapest available proxy for normalisation
        quality: a target producing far more changes than its documents
        plausibly have is a target with broken selectors.
        """
        query = "SELECT COUNT(*) AS polls, SUM(changed) AS changes FROM poll"
        params = ()
        if check_name:
            query += " WHERE check_name = ?"
            params = (check_name,)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()

        blobs = total = 0
        for dirpath, _, names in os.walk(self.blob_root):
            for name in names:
                blobs += 1
                total += os.path.getsize(os.path.join(dirpath, name))
        return {
            "polls": row["polls"] or 0,
            "changes": row["changes"] or 0,
            "blobs": blobs,
            "bytes": total,
        }
