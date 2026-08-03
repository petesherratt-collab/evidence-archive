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


SCHEMA_VERSION = 4
GENESIS = "0" * 64

# Chain versions are deliberately NOT the schema version. The version is folded
# into every record hash, so bumping it rewrites what a correct chain looks like
# and invalidates every archive recorded before the bump. Adding a table is a
# schema change; it is not a change to how a poll record hashes. Keep these
# pinned unless you intend to break existing chains, and migrate deliberately if
# you ever do.
POLL_CHAIN_VERSION = 1
NORMALISATION_CHAIN_VERSION = 1
ANNOTATION_CHAIN_VERSION = 1

# What the anchor commits to. Bumping this changes every future anchor value,
# so it may only move while nothing has been anchored yet, or with a documented
# migration. Moved 1 -> 2 when the annotation chain was added: leaving
# annotations outside the anchor would have let a correction be retracted
# silently, which is the one thing a correction must not be.
COMBINED_HEAD_VERSION = 2

# Fingerprint of how the fetcher behaves, recorded against every poll from the
# point it was introduced. This is NOT a chain version and does not affect how
# rows hash; it is data about the regime that produced the row.
#
#   1  kibitzr's stock retry loop, which is dead on Python 3.10+ —
#      sleep_on_exception raises AttributeError on collections.Callable while
#      handling a retriable error, so the first transient failure is fatal to
#      the poll and is recorded with the wrong cause.
#   2  retry loop restored (see promoter.CapturingSessionFetcher), so a
#      transient failure is retried before it is recorded as a failure.
#
# Bump this whenever a change alters WHEN a poll succeeds or fails. A reader
# comparing failure rates across a bump is comparing two different instruments.
FETCH_SEMANTICS_VERSION = 3

#: What changed at each fetch-semantics version, in the words the annotation
#: chain will carry. Kept beside the number so a bump cannot land without
#: saying what it did to the success/failure boundary — the annotation is the
#: only thing telling a reader that failure counts either side are not
#: comparable.
FETCH_SEMANTICS_NOTES = {
    2: ("retry loop restored over upstream's removed collections.Callable; "
        "transient fetch errors are now retried before being recorded as "
        "failures, so failure counts are not comparable across this point"),
    3: ("fetch path hardened: the response cache was removed so every poll "
        "reaches the origin, redirects are followed one vetted hop at a time, "
        "connections are pinned to the addresses that were vetted, and the "
        "fetch is bounded by size and wall-clock deadline. A poll that would "
        "previously have been answered from cache, followed off-origin, or "
        "run unbounded now fails instead — so failure counts, and the meaning "
        "of a successful poll, are not comparable across this point"),
}

# Annotation kinds. Annotations are assertions ABOUT the poll log, appended to
# their own chain; they never modify a poll row.
ANNOTATION_KINDS = ("correction", "fetch_regime", "schedule", "note")

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

-- The normalised counterpart of a poll: the hash of the content AFTER the
-- transform chain has selected and reduced it, chained separately so that
-- "the document changed" is recoverable independently of "the bytes changed".
CREATE TABLE IF NOT EXISTS normalisation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id         INTEGER,
    check_name      TEXT    NOT NULL,
    recorded_at     TEXT    NOT NULL,
    content_sha256  TEXT    NOT NULL,
    content_length  INTEGER,
    transform_id    TEXT    NOT NULL,
    changed         INTEGER NOT NULL,
    prev_hash       TEXT    NOT NULL,
    record_hash     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS norm_check_idx ON normalisation (check_name, id);

-- Assertions about the poll log, on their own chain.
--
-- The poll log is append-only and its rows are hashed, so a row that turns out
-- to be misleading cannot be corrected by editing it — that is the property the
-- chain exists to provide, and spending it to tidy up a bad record would be a
-- worse loss than the bad record. Corrections are therefore appended here and
-- read alongside the rows they describe.
--
-- Three things live here, and they are the same shape: something true about the
-- collector rather than about the target.
--
--   correction    rows N..M say X; the truth was Y
--   fetch_regime  from time T the fetcher behaves like this
--   schedule      check C was INTENDED to poll every P seconds from time T
--
-- `effective_from` is when the asserted fact became true; `recorded_at` is when
-- we wrote it down. They are usually different, and conflating them would let
-- the archive claim it knew something earlier than it did.
CREATE TABLE IF NOT EXISTS annotation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT    NOT NULL,
    check_name      TEXT,
    effective_from  TEXT    NOT NULL,
    recorded_at     TEXT    NOT NULL,
    subject_from    INTEGER,
    subject_to      INTEGER,
    detail          TEXT    NOT NULL,
    prev_hash       TEXT    NOT NULL,
    record_hash     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS annotation_kind_idx ON annotation (kind, id);

-- External timestamp proofs over the chain heads.
--
-- The chains prove internal consistency and nothing whatever about time: a
-- self-consistent history can be manufactured wholesale after the fact. Until a
-- head has been committed to something outside this machine, the archive is a
-- well-built database rather than evidence.
--
-- The constituent heads are stored alongside the combined value on purpose.
-- `combined_head` is a formula that may change — it already went from v1 to v2
-- when the annotation chain was added — and an anchor taken under an older
-- formula must stay independently checkable without reimplementing that
-- version. Proofs accumulate; a later schema change adds proofs rather than
-- invalidating earlier ones, which is exactly why anchoring early is safe.
CREATE TABLE IF NOT EXISTS anchor (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name      TEXT    NOT NULL,
    anchored_at     TEXT    NOT NULL,
    head_version    INTEGER NOT NULL,
    combined_head   TEXT    NOT NULL,
    poll_head       TEXT    NOT NULL,
    norm_head       TEXT    NOT NULL,
    annotation_head TEXT    NOT NULL,
    last_poll_id    INTEGER,
    method          TEXT    NOT NULL,
    manifest_ref    TEXT    NOT NULL,
    manifest_sha256 TEXT    NOT NULL,
    proof_ref       TEXT,
    status          TEXT    NOT NULL,
    detail          TEXT
);
CREATE INDEX IF NOT EXISTS anchor_check_idx ON anchor (check_name, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Columns added to `poll` after the first release. SQLite has no
# ADD COLUMN IF NOT EXISTS, so these are applied by inspection.
_POLL_ADDED_COLUMNS = (
    ("fetch_id", "TEXT"),
)


def sha256_hex(data):
    """Return hex SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def utc_now_iso():
    """Return current UTC time as a second-precision ISO 8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_record_hash(fields, prev_hash, version=POLL_CHAIN_VERSION):
    """Hash a record's identifying fields chained onto the previous record.

    Serialisation is canonical (sorted keys, no whitespace) so the value
    is reproducible by anyone holding the same row.
    """
    payload = dict(fields, v=version, prev=prev_hash)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return sha256_hex(encoded.encode('utf-8'))


def poll_hash_fields(check_name, url, polled_at, ok, http_status,
                     content_sha256, changed, fetch_id_=None):
    """Return the identifying fields of a poll row, in hashed form.

    Written once and used by both the writer and the verifier, because the two
    computing the same payload is the entire basis of the chain and two copies
    of the field list would eventually disagree.

    ``fetch_id`` is included **only when present**. Rows written before fetch
    behaviour was fingerprinted have no such value and hash exactly as they did
    when they were written, so an existing archive still verifies after the
    upgrade. Absence is unforgeable in the direction that matters: a row's hash
    commits to whether it carried a fetch_id, so one cannot be stripped from a
    row after the fact.
    """
    fields = {
        "check": check_name,
        "url": url,
        "polled_at": polled_at,
        "ok": bool(ok),
        "http_status": http_status,
        "content_sha256": content_sha256,
        "changed": bool(changed),
    }
    if fetch_id_ is not None:
        fields["fetch_id"] = fetch_id_
    return fields


def fetch_id(conf=None, semantics=FETCH_SEMANTICS_VERSION):
    """Fingerprint the regime a poll was fetched under.

    The counterpart of ``transform_id``, for the other half of the pipeline.
    ``transform_id`` exists so that retuning a selector is distinguishable from
    the document changing; this exists so that changing the fetcher is
    distinguishable from the target's availability changing.

    That matters most for the failure series. Fixing a broken retry loop does
    not touch a single byte of collected content, but it does change when a poll
    is recorded as failed — so failure counts before and after are not
    comparable, and without this a reader would have to correlate against a git
    history they may not hold in order to find that out.

    Covers what can move the success/failure boundary: the fetch semantics
    version, whether a browser was driven, and the identity presented to the
    server (a blocked User-Agent shows up as the target refusing us).
    """
    conf = conf or {}
    payload = {
        "v": semantics,
        "firefox": bool(conf.get("firefox") or conf.get("browser")),
        "user_agent": conf.get("user_agent", None),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                         default=str)
    return sha256_hex(encoded.encode('utf-8'))


def transform_id(transform_conf):
    """Fingerprint a check's transform rules.

    Recorded against every normalised observation so that a change in the
    extraction rules is distinguishable from a change in the document. Without
    it, retuning a selector and the document being edited look identical in the
    log — and the first is by far the more common cause of a diff.
    """
    encoded = json.dumps(transform_conf or [], sort_keys=True,
                         separators=(',', ':'), default=str)
    return sha256_hex(encoded.encode('utf-8'))


class PollRecord:
    """Outcome of recording one poll."""

    __slots__ = ('changed', 'content_sha256', 'raw_ref', 'record_hash',
                 'polled_at', 'poll_id')

    def __init__(self, changed, content_sha256, raw_ref, record_hash, polled_at,
                 poll_id=None):
        self.changed = changed
        self.content_sha256 = content_sha256
        self.raw_ref = raw_ref
        self.record_hash = record_hash
        self.polled_at = polled_at
        self.poll_id = poll_id

    def __repr__(self):
        return (f"PollRecord(changed={self.changed}, "
                f"content_sha256={self.content_sha256!r}, "
                f"record_hash={self.record_hash!r})")


class NormalisationRecord:
    """Outcome of recording one post-transform observation."""

    __slots__ = ('changed', 'content_sha256', 'record_hash', 'recorded_at',
                 'transform_id', 'poll_id')

    def __init__(self, changed, content_sha256, record_hash, recorded_at,
                 transform_id_, poll_id):
        self.changed = changed
        self.content_sha256 = content_sha256
        self.record_hash = record_hash
        self.recorded_at = recorded_at
        self.transform_id = transform_id_
        self.poll_id = poll_id

    def __repr__(self):
        return (f"NormalisationRecord(changed={self.changed}, "
                f"content_sha256={self.content_sha256!r})")


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
        """Create or upgrade the schema in place.

        Upgrading is additive only — every statement is CREATE ... IF NOT
        EXISTS, so an archive written by an earlier version gains the new table
        and keeps every existing row. Poll chains recorded before the upgrade
        still verify, because the chain version they were hashed with has not
        moved. See the note on POLL_CHAIN_VERSION.

        Columns added to `poll` after the fact are applied here too. They are
        nullable and excluded from the hash when null, so rows written before
        the column existed continue to verify unchanged.
        """
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            existing = {row["name"]
                        for row in conn.execute("PRAGMA table_info(poll)")}
            for column, sql_type in _POLL_ADDED_COLUMNS:
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE poll ADD COLUMN {column} {sql_type}")
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
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
                    error=None, polled_at=None, fetch_id_=None):
        """Append one poll to the log, retaining content if it is new.

        ``content`` is the response as fetched, before any transform. Pass
        None for a failed fetch; the poll is still logged, with ``changed``
        false, so the gap is visible as an attempted observation.

        ``fetch_id_`` fingerprints the fetch regime this poll was made under;
        see ``fetch_id``. Omitting it is allowed and hashes as it always did.
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

            fields = poll_hash_fields(check_name, url, polled_at, ok,
                                      http_status, digest, changed, fetch_id_)
            record_hash = compute_record_hash(fields, prev_hash)

            cur = conn.execute(
                "INSERT INTO poll (check_name, url, polled_at, ok, http_status,"
                " content_length, content_sha256, etag, last_modified, changed,"
                " raw_ref, error, fetch_id, prev_hash, record_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (check_name, url, polled_at, int(bool(ok)), http_status,
                 len(raw) if raw is not None else None, digest, etag,
                 last_modified, int(changed), raw_ref, error, fetch_id_,
                 prev_hash, record_hash),
            )
            poll_id = cur.lastrowid

        return PollRecord(changed, digest, raw_ref, record_hash, polled_at,
                          poll_id)

    # -- normalised observations -----------------------------------------

    def last_poll_id(self, check_name):
        """Return the id of the most recent poll for a check, or None."""
        row = self.last_poll(check_name)
        return row["id"] if row else None

    def record_normalisation(self, check_name, content, transform_conf=None,
                             poll_id=None, recorded_at=None):
        """Append the hash of post-transform content to its own chain.

        ``content`` is what the transform chain produced *before* any reporting
        transform turned it into a diff — that is, the normalised document, not
        the report about it.

        This exists because the poll log's ``changed`` is computed on raw bytes,
        and raw bytes move for reasons that have nothing to do with the
        document: CSP nonces, ASP.NET viewstate, rotating banners. Recorded
        here, ``changed`` means the selected content moved, which is the
        question anyone reading the archive is actually asking.

        No blob is stored. The raw response is already retained, and the
        transform rules are fingerprinted, so the normalised content is
        re-derivable rather than needing its own copy.
        """
        recorded_at = recorded_at or utc_now_iso()
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if raw is None:
            return None
        digest = sha256_hex(raw)
        fingerprint = transform_id(transform_conf)

        with self._connect() as conn:
            if poll_id is None:
                poll_id = self.last_poll_id(check_name)

            previous = conn.execute(
                "SELECT content_sha256 FROM normalisation "
                "WHERE check_name = ? ORDER BY id DESC LIMIT 1",
                (check_name,),
            ).fetchone()
            changed = bool(previous is None
                           or previous["content_sha256"] != digest)

            last = conn.execute(
                "SELECT record_hash FROM normalisation WHERE check_name = ? "
                "ORDER BY id DESC LIMIT 1",
                (check_name,),
            ).fetchone()
            prev_hash = last["record_hash"] if last else GENESIS

            fields = {
                "check": check_name,
                "poll_id": poll_id,
                "recorded_at": recorded_at,
                "content_sha256": digest,
                "transform_id": fingerprint,
                "changed": changed,
            }
            record_hash = compute_record_hash(
                fields, prev_hash, version=NORMALISATION_CHAIN_VERSION)

            conn.execute(
                "INSERT INTO normalisation (poll_id, check_name, recorded_at,"
                " content_sha256, content_length, transform_id, changed,"
                " prev_hash, record_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (poll_id, check_name, recorded_at, digest, len(raw),
                 fingerprint, int(changed), prev_hash, record_hash),
            )

        return NormalisationRecord(changed, digest, record_hash, recorded_at,
                                   fingerprint, poll_id)

    # -- annotations -----------------------------------------------------

    def record_annotation(self, kind, detail, check_name=None,
                          effective_from=None, subject_from=None,
                          subject_to=None, recorded_at=None):
        """Append an assertion about the log to the annotation chain.

        This is the only sanctioned way to correct the record. A poll row that
        turns out to be misleading stays exactly as written and gains an
        annotation pointing at it; the alternative — editing the row — would
        break the chain, and an archive that edits itself when the contents
        embarrass it has no claim on anyone's trust. Being able to say "we found
        bad records and appended a correction" is worth more than never having
        had one.

        ``detail`` is any JSON-serialisable object and is canonicalised before
        hashing, so a third party holding the row can recompute the hash.
        """
        if kind not in ANNOTATION_KINDS:
            raise ValueError(
                f"unknown annotation kind {kind!r}; "
                f"expected one of {', '.join(ANNOTATION_KINDS)}")
        recorded_at = recorded_at or utc_now_iso()
        effective_from = effective_from or recorded_at
        encoded_detail = json.dumps(detail, sort_keys=True,
                                    separators=(',', ':'), default=str)

        with self._connect() as conn:
            last = conn.execute(
                "SELECT record_hash FROM annotation ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = last["record_hash"] if last else GENESIS

            fields = {
                "kind": kind,
                "check": check_name,
                "effective_from": effective_from,
                "recorded_at": recorded_at,
                "subject_from": subject_from,
                "subject_to": subject_to,
                "detail": encoded_detail,
            }
            record_hash = compute_record_hash(
                fields, prev_hash, version=ANNOTATION_CHAIN_VERSION)

            conn.execute(
                "INSERT INTO annotation (kind, check_name, effective_from,"
                " recorded_at, subject_from, subject_to, detail, prev_hash,"
                " record_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (kind, check_name, effective_from, recorded_at, subject_from,
                 subject_to, encoded_detail, prev_hash, record_hash),
            )
        return record_hash

    def annotations(self, kind=None, check_name=None):
        """Return annotation rows, oldest first, with ``detail`` decoded."""
        query = "SELECT * FROM annotation"
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if check_name:
            # A NULL check_name is an annotation about every check, so it is
            # part of the answer for any particular one.
            clauses.append("(check_name = ? OR check_name IS NULL)")
            params.append(check_name)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(row["detail"])
            out.append(item)
        return out

    def last_annotation(self, kind, check_name=None):
        """Return the most recent annotation of a kind for a check, or None."""
        matching = [a for a in self.annotations(kind=kind)
                    if a["check_name"] == check_name]
        return matching[-1] if matching else None

    def declare_schedule(self, check_name, period, effective_from=None):
        """Record the period a check is INTENDED to poll at, if it has changed.

        Every poll writes a row, so absence of rows already means absence of
        polling — the log can tell "checked, unchanged" from "not checked".
        What it cannot tell unaided is whether we *meant* to be watching, so a
        hole reads ambiguously between "not scheduled yet" and "scheduled, and
        the machine was off". Only the second is a gap in coverage.

        Recording intent as data resolves that: a gap can then be read against
        the schedule in force at the time rather than guessed at.

        Returns the annotation hash if one was written, else None.
        """
        previous = self.last_annotation("schedule", check_name)
        if previous and previous["detail"].get("period") == period:
            return None
        return self.record_annotation(
            "schedule",
            {"period": period},
            check_name=check_name,
            effective_from=effective_from,
        )

    def declare_fetch_regime(self, fingerprint, semantics, note,
                             check_name=None, effective_from=None):
        """Record a change in fetch behaviour, if it has changed.

        The fingerprint on each poll row makes the discontinuity *detectable*;
        this makes it *legible*. A reader who sees the fetch_id move should not
        have to reverse-engineer what moved.

        Returns the annotation hash if one was written, else None.
        """
        previous = self.last_annotation("fetch_regime", check_name)
        if previous and previous["detail"].get("fetch_id") == fingerprint:
            return None
        return self.record_annotation(
            "fetch_regime",
            {"fetch_id": fingerprint, "semantics": semantics, "note": note},
            check_name=check_name,
            effective_from=effective_from,
        )

    def gaps(self, check_name, tolerance=2.0):
        """Return intervals between polls that exceed the declared schedule.

        Each gap is reported against the period that was in force when it
        started, so a gap opened under a 6-hourly schedule is not judged by a
        12-hourly one declared later. Intervals with no schedule in force are
        returned with ``period: None`` — unjudgeable rather than silently fine,
        because "we never said we were watching" is a real answer and must not
        be confused with "we were watching and nothing happened".
        """
        schedules = [a for a in self.annotations("schedule")
                     if a["check_name"] in (check_name, None)]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, polled_at FROM poll WHERE check_name = ?"
                " ORDER BY polled_at, id",
                (check_name,),
            ).fetchall()

        def period_at(when):
            active = [s for s in schedules if s["effective_from"] <= when]
            return active[-1]["detail"].get("period") if active else None

        out = []
        for earlier, later in zip(rows, rows[1:]):
            start = datetime.fromisoformat(earlier["polled_at"])
            end = datetime.fromisoformat(later["polled_at"])
            seconds = (end - start).total_seconds()
            period = period_at(earlier["polled_at"])
            if period and seconds > period * tolerance:
                out.append({
                    "check_name": check_name,
                    "from_poll": earlier["id"],
                    "to_poll": later["id"],
                    "from": earlier["polled_at"],
                    "to": later["polled_at"],
                    "seconds": seconds,
                    "period": period,
                })
        return out

    # -- anchors ---------------------------------------------------------

    def head_components(self, check_name):
        """Return the three chain heads and the value committing to them.

        Returned as data rather than just the digest so that an anchor can
        record what it committed to, not only the result of committing.
        """
        poll_head = self.head(check_name)
        if poll_head is None:
            return None
        return {
            "check": check_name,
            "poll_head": poll_head,
            "norm_head": self.normalisation_head(check_name) or GENESIS,
            "annotation_head": self.annotation_head() or GENESIS,
            "head_version": COMBINED_HEAD_VERSION,
            "combined_head": self.combined_head(check_name),
            "last_poll_id": self.last_poll_id(check_name),
        }

    def record_anchor(self, components, method, manifest_ref, manifest_sha256,
                      proof_ref=None, status="pending", detail=None,
                      anchored_at=None):
        """Record that a set of heads was submitted for external timestamping."""
        anchored_at = anchored_at or utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO anchor (check_name, anchored_at, head_version,"
                " combined_head, poll_head, norm_head, annotation_head,"
                " last_poll_id, method, manifest_ref, manifest_sha256,"
                " proof_ref, status, detail)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (components["check"], anchored_at, components["head_version"],
                 components["combined_head"], components["poll_head"],
                 components["norm_head"], components["annotation_head"],
                 components["last_poll_id"], method, manifest_ref,
                 manifest_sha256, proof_ref, status,
                 json.dumps(detail, sort_keys=True, separators=(',', ':'),
                            default=str) if detail is not None else None),
            )
            return cur.lastrowid

    def anchors(self, check_name=None, status=None):
        """Return anchor rows, oldest first."""
        query = "SELECT * FROM anchor"
        clauses, params = [], []
        if check_name:
            clauses.append("check_name = ?")
            params.append(check_name)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    def set_anchor_status(self, manifest_ref, status, detail=None):
        """Update every anchor row sharing a manifest. Returns rows changed."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE anchor SET status = ?, detail = COALESCE(?, detail)"
                " WHERE manifest_ref = ?",
                (status,
                 json.dumps(detail, sort_keys=True, separators=(',', ':'),
                            default=str) if detail is not None else None,
                 manifest_ref),
            )
            return cur.rowcount

    def unanchored_polls(self, check_name):
        """Count polls recorded since this check's most recent anchor.

        The honest measure of exposure: these are the observations for which no
        external proof of existence-by-a-given-time yet exists.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(last_poll_id) AS last FROM anchor"
                " WHERE check_name = ? AND status != 'failed'",
                (check_name,),
            ).fetchone()
            last = row["last"] if row and row["last"] is not None else 0
            return conn.execute(
                "SELECT COUNT(*) AS n FROM poll"
                " WHERE check_name = ? AND id > ?",
                (check_name, last),
            ).fetchone()["n"]

    # -- integrity -------------------------------------------------------

    def head(self, check_name):
        """Return the current poll-chain head for a check, or None."""
        row = self.last_poll(check_name)
        return row["record_hash"] if row else None

    def normalisation_head(self, check_name):
        """Return the current normalisation-chain head for a check, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_hash FROM normalisation WHERE check_name = ? "
                "ORDER BY id DESC LIMIT 1",
                (check_name,),
            ).fetchone()
        return row["record_hash"] if row else None

    def annotation_head(self):
        """Return the current annotation-chain head, or None.

        One global chain rather than one per check: annotations are few, and
        some of them (a fetch-regime change) are true of every check at once.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_hash FROM annotation ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["record_hash"] if row else None

    def combined_head(self, check_name):
        """Return one value committing to all three chains, or None if no polls.

        This is the value to submit for external timestamping. Anchoring one
        chain would leave the others free to be rewritten, so the anchor covers
        all of them — including annotations, so that a correction, once
        anchored, cannot quietly be withdrawn.

        Reproducible by a third party as::

            sha256('{"ann":"<ann_head>","norm":"<norm_head>",'
                   '"poll":"<poll_head>","v":2}')

        with an unanchored chain represented by the all-zero genesis value.
        The annotation head is global, so it is the same for every check in a
        batch; the poll and normalisation heads are per check.
        """
        poll_head = self.head(check_name)
        if poll_head is None:
            return None
        payload = {
            "poll": poll_head,
            "norm": self.normalisation_head(check_name) or GENESIS,
            "ann": self.annotation_head() or GENESIS,
            "v": COMBINED_HEAD_VERSION,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return sha256_hex(encoded.encode('utf-8'))

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
            fields = poll_hash_fields(
                row["check_name"], row["url"], row["polled_at"], row["ok"],
                row["http_status"], row["content_sha256"], row["changed"],
                row["fetch_id"],
            )
            if compute_record_hash(fields, prev_hash) != row["record_hash"]:
                return False, row["id"]
            prev_hash = row["record_hash"]
        return True, None

    def verify_normalisation_chain(self, check_name):
        """Recompute the normalisation hash chain for a check.

        Same contract as ``verify_chain``: (True, None) or (False, bad_row_id).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM normalisation WHERE check_name = ? ORDER BY id",
                (check_name,),
            ).fetchall()

        prev_hash = GENESIS
        for row in rows:
            if row["prev_hash"] != prev_hash:
                return False, row["id"]
            fields = {
                "check": row["check_name"],
                "poll_id": row["poll_id"],
                "recorded_at": row["recorded_at"],
                "content_sha256": row["content_sha256"],
                "transform_id": row["transform_id"],
                "changed": bool(row["changed"]),
            }
            expected = compute_record_hash(
                fields, prev_hash, version=NORMALISATION_CHAIN_VERSION)
            if expected != row["record_hash"]:
                return False, row["id"]
            prev_hash = row["record_hash"]
        return True, None

    def verify_annotation_chain(self):
        """Recompute the annotation hash chain.

        Same contract as ``verify_chain``: (True, None) or (False, bad_row_id).
        A retracted correction is exactly as detectable as a doctored poll.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM annotation ORDER BY id").fetchall()

        prev_hash = GENESIS
        for row in rows:
            if row["prev_hash"] != prev_hash:
                return False, row["id"]
            fields = {
                "kind": row["kind"],
                "check": row["check_name"],
                "effective_from": row["effective_from"],
                "recorded_at": row["recorded_at"],
                "subject_from": row["subject_from"],
                "subject_to": row["subject_to"],
                "detail": row["detail"],
            }
            expected = compute_record_hash(
                fields, prev_hash, version=ANNOTATION_CHAIN_VERSION)
            if expected != row["record_hash"]:
                return False, row["id"]
            prev_hash = row["record_hash"]
        return True, None

    def stats(self, check_name=None):
        """Return (polls, changes, blobs, bytes_on_disk) for reporting.

        Growth rate is the cheapest available proxy for normalisation
        quality: a target producing far more changes than its documents
        plausibly have is a target with broken selectors.
        """
        query = ("SELECT COUNT(*) AS polls, SUM(changed) AS changes,"
                 " COUNT(DISTINCT fetch_id) AS fetches,"
                 " SUM(CASE WHEN fetch_id IS NULL THEN 1 ELSE 0 END)"
                 "   AS unfingerprinted,"
                 " SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures"
                 " FROM poll")
        norm_query = ("SELECT COUNT(*) AS n, SUM(changed) AS changes,"
                      " COUNT(DISTINCT transform_id) AS transforms"
                      " FROM normalisation")
        params = ()
        if check_name:
            query += " WHERE check_name = ?"
            norm_query += " WHERE check_name = ?"
            params = (check_name,)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
            norm = conn.execute(norm_query, params).fetchone()

        blobs = total = 0
        for dirpath, _, names in os.walk(self.blob_root):
            for name in names:
                blobs += 1
                total += os.path.getsize(os.path.join(dirpath, name))
        return {
            "polls": row["polls"] or 0,
            "changes": row["changes"] or 0,
            "failures": row["failures"] or 0,
            "unfingerprinted": row["unfingerprinted"] or 0,
            # More than one fetch regime means the fetcher itself changed
            # mid-series, so a shift in the FAILURE rate across that point may
            # be ours rather than the target's.
            #
            # Rows predating the fingerprint count as one regime between them.
            # SQL's COUNT(DISTINCT) ignores NULLs, and taking that at face value
            # would have hidden the most consequential regime change this
            # archive has had — the one that introduced the fingerprint — behind
            # a count of 1. An era we cannot name is still an era.
            "fetch_revisions": ((row["fetches"] or 0)
                                + (1 if row["unfingerprinted"] else 0)),
            # Normalised counts are the ones to judge a selector by. `changes`
            # above moves with the raw bytes and will be inflated by per-request
            # nonces and viewstate on many real sites.
            "normalised": norm["n"] or 0,
            "normalised_changes": norm["changes"] or 0,
            # More than one distinct transform fingerprint means the extraction
            # rules were retuned mid-series, so some diffs are ours, not theirs.
            "transform_revisions": norm["transforms"] or 0,
            "blobs": blobs,
            "bytes": total,
        }
