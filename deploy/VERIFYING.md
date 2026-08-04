# Verifying this archive without trusting its keeper

This document is the specification. The code in `tools/kibitzr-archive/` is one
implementation of it, and nothing here requires you to run, read or trust that
code. If the two ever disagree, the disagreement is itself a finding.

Everything below can be done with `sha256sum`, a JSON library, `sqlite3`, and
the OpenTimestamps client.

## What you need

- `polls.db` — the poll log
- `anchors/*.json` — the manifests that were externally timestamped
- `anchors/*.json.ots` — the timestamp proofs
- `pip install opentimestamps-client`

## 1. Verify the timestamp proof

```sh
ots verify anchors/<stamp>.json.ots
```

This tells you the Bitcoin block that commits to the manifest, and therefore
the latest time by which that manifest existed. It reads `anchors/<stamp>.json`
from disk and hashes it, so it fails if the manifest was altered by so much as
one byte after stamping.

A proof that reports **pending** has not yet been committed to Bitcoin. It rests
on the OpenTimestamps calendar servers, which is a weaker claim. `archive
anchors` reports which state each proof is in, and does not describe a pending
proof as complete.

**Without the client or a node**, you can still read *which bytes* a proof
covers. A detached proof carries the digest of the stamped file immediately
after its header:

```
\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94
<version varint> <hash-op byte: 0x08 = sha256> <32-byte digest>
```

Compare that digest against `sha256` of the manifest. `archive fsck` and
`verify_independently.py` both do this, and it is the link the rest of the
chain hangs from — the proof is the only digest in the archive that does not
live in `polls.db`, so it is the only one an archivist rewriting the database
cannot reach.

It establishes *which bytes*, not *when*. A proof re-stamped today over an
altered manifest passes this check, carrying today's attestation instead of the
original's. Only `ots verify` and the block time it reports separate those, so
compare the attested time against the manifest's `created_at`.

## 2. Check the manifest against the archive

The manifest names, for each check, the three chain heads that were anchored:

```json
{"checks":[{"annotation_head":"...","check":"...","combined_head":"...",
 "head_version":2,"last_poll_id":50,"norm_head":"...","poll_head":"..."}],
 "combined_head_version":2,"created_at":"...","manifest_version":1}
```

Recompute those heads from `polls.db` using section 3 and compare. If they
match, every poll up to `last_poll_id` existed at the time the proof attests.

**Check them in this order, and take each value from the link before it:**

```
.ots proof  ->  manifest bytes  ->  heads and last_poll_id in the manifest
            ->  chains recomputed from polls.db
```

Specifically: rebuild the check's poll chain, recompute the hash **at the row
whose id is `last_poll_id`**, and require it to equal `poll_head`. Require
`norm_head` and `annotation_head` to occur in their own fully recomputed
chains. Only then recompute `combined_head` from the three.

Two things this ordering rules out, both of which passed before 4 Aug 2026:

- **Do not compare a manifest head against a stored `record_hash`, and do not
  consult the `anchor` table at all.** That table is an index, not evidence: it
  lives in the same database as the rows an anchor exists to pin down, so a log
  rewritten consistently — fields edited, hashes recomputed down the chain, the
  anchor row updated to agree — satisfies any check made against it. The
  manifest file is the only copy of the head that was actually stamped.
- **Checking that `combined_head` follows from the three heads printed beside
  it establishes nothing.** A manifest is self-consistent by construction. That
  arithmetic is worth checking only after each of its inputs has been located
  in a chain rebuilt from the log.

The constituent heads are recorded, not just `combined_head`, precisely so this
step never depends on reimplementing the combining formula of whatever version
was in force. An anchor taken under `head_version` 1 stays checkable after the
formula moves to 2.

## 3. Recompute the chains

### Canonical serialisation

Every hash in this archive is computed the same way:

1. Build the field mapping given below.
2. Add `"v"` (chain version, integer) and `"prev"` (previous record's
   `record_hash`, or 64 zeros for the first record in a chain).
3. Serialise as JSON with **sorted keys**, **no insignificant whitespace**
   (`,` and `:` separators), and **non-ASCII escaped** as `\uXXXX` — i.e.
   Python's `json.dumps(payload, sort_keys=True, separators=(',',':'))` with
   `ensure_ascii` at its default.
4. SHA-256 the resulting bytes; take lowercase hex.

The escaping rule is load-bearing here: check names contain `£` and `—`. The
same rule applies to the anchor manifests, so the whole archive has one
canonicalisation, not two.

### Poll chain (`v: 1`)

Rows from `poll` for one `check_name`, ordered by `id`:

| key | column |
|---|---|
| `check` | `check_name` |
| `url` | `url` |
| `polled_at` | `polled_at` |
| `ok` | `ok` as a JSON boolean |
| `http_status` | `http_status` |
| `content_sha256` | `content_sha256` |
| `changed` | `changed` as a JSON boolean |
| `fetch_id` | `fetch_id` — **omit this key entirely when the column is NULL** |

Omitting `fetch_id` when absent is what lets rows written before that column
existed keep verifying. It is not a hole: a row's hash commits to whether it
carried a `fetch_id`, so one cannot be removed after the fact without breaking
the chain.

Note which columns are **not** hashed: `etag`, `last_modified`, `content_length`,
`raw_ref`, and `error`. They are recorded for use, not attested. In particular
**an `error` string is not covered by the chain** — treat recorded error text as
context, not as evidence. Corrections to it belong on the annotation chain.

### Normalisation chain (`v: 1`)

Rows from `normalisation` for one `check_name`, ordered by `id`:
`check`, `poll_id`, `recorded_at`, `content_sha256`, `transform_id`, `changed`.

### Annotation chain (`v: 1`)

One global chain. All rows from `annotation` ordered by `id`:
`kind`, `check` (from `check_name`, null for archive-wide), `effective_from`,
`recorded_at`, `subject_from`, `subject_to`, `detail`.

`detail` is hashed as the **stored JSON string**, not as a re-serialised object.

### Combined head (`v: 2`)

```
sha256(json.dumps({"ann":<annotation_head>,"norm":<norm_head>,
                   "poll":<poll_head>,"v":2},
                  sort_keys=True, separators=(',',':')))
```

with an empty chain represented by 64 zeros. Version 1 was the same without
`"ann"`; it appears only in anchors taken before the annotation chain existed,
and there are none.

## 4. Re-derive the content

`content_sha256` on a poll row is the SHA-256 of the response **as fetched**,
before any transform. The bytes are in `blobs/<first two hex chars>/<digest>.gz`.

```sh
zcat blobs/ab/abcdef...gz | sha256sum
```

**Locate the blob by `content_sha256`, never by `raw_ref`.** Section 3 lists
`raw_ref` among the columns that are not hashed, so a blob reached through it
is bound to nothing: an archive holding a forged response under its own true
digest, with `raw_ref` repointed at it and the original deleted, contradicts
the anchored row while satisfying every check that resolves through `raw_ref`.
`content_sha256` is hashed into the poll chain, so reaching the bytes through
it is what ties them to the proof. The two columns have always held the same
digest; a row where they differ is a finding, not a variant.

This paragraph is the specification's original position and was always correct.
It is spelled out because *both* implementations — `kibitzr archive fsck` and
`verify_independently.py` — resolved blobs through `raw_ref` until 4 Aug 2026.
Two independent implementations agreeing is not evidence that either matches
the spec.

```sh
python3 deploy/audit_retained_responses.py archive   # every poll, this check
```

So you can check the extraction rather than taking the extracted text on trust:
re-run the check's transform over the retained response and compare against the
`normalisation` row.

## What a valid verification does and does not establish

**Does:**

- The rows you hold are the rows that were hashed (integrity).
- No row was inserted, removed or reordered within a chain (continuity).
- Everything up to the anchored head existed no later than the attested Bitcoin
  block (time).
- The extracted content follows from the retained raw response (derivation).

**Does not:**

- That the archive is **complete**. Every poll writes a row, so silence means
  nobody polled — but nothing here proves the collector was running when it
  should have been. `archive gaps` reads holes against the *declared* schedule,
  which is a statement of intent by the keeper, not evidence of uptime.
- That anything collected is **true**. The archive attests what a URL returned
  and when it was fetched, nothing about whether the page was accurate.
- Anything at all **before the first anchor**. Rows predating it are internally
  consistent and could, on this evidence alone, have been written at any time.
  First anchor: 2026-08-02. Collection began 2026-08-01.
- That a **pending** proof will complete. Until upgraded it depends on the
  calendar operators.

## Reporting a discrepancy

If a chain does not verify, or a manifest does not match its proof, that is
exactly what this design is for: open an issue with the row id and your
recomputed hash. A log that cannot incriminate its own keeper is not doing its
job, so a genuine failure here is more useful than a clean result.
