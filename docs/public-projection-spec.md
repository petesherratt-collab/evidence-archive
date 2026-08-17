# Public projection specification

This document defines the archive-to-public projection independently of the
exporter implementation. It is the contract that an independent verifier
reconstructs from `polls.db` and retained response blobs. It defines what is
eligible for publication; it does not claim that a publisher was truthful or
that the collector polled continuously.

## Target eligibility

The configured public-domain intelligence sources are the five named
procurement or public-publication targets below. Their IDs are the ASCII,
lower-case, punctuation-to-hyphen slugs of the exact check name.

| Check name | Public ID | Source type | Structured projection |
| --- | --- | --- | --- |
| Contracts Finder — recent awards | `contracts-finder-recent-awards` | `ocds_api` | OCDS awards |
| Find a Tender — recent awards | `find-a-tender-recent-awards` | `ocds_api` | OCDS releases tagged `award` |
| Government Commercial Agency — agreements | `government-commercial-agency-agreements` | `html_publication` | none; HTML/text only |
| Departmental spend over £25k — monthly release | `departmental-spend-over-25k-monthly-release` | `html_publication` | none; HTML/text only |
| UK direct awards — no competition | `uk-direct-awards-no-competition` | `ocds_api` | OCDS releases whose tender procurement method is `direct` |

The operational control is a check whose annotation chain contains a `note`
whose JSON `detail.role` is `control`. The current control is **Control —
collector liveness**, with ID `control-collector-liveness`. It is exported as a
separate control source and its observations remain available for operational
audits. A control is never a procurement source, procurement record, entity,
or public BI event.

Only the five named intelligence checks and annotated controls are eligible
for this public contract. Any other check name is an unsupported archive
target and must not be silently projected as public intelligence.

Every poll row for an eligible check is an eligible observation, including a
failed poll. The observation ID is `<source_id>:poll:<poll.id>`, and its
sequence is the one-based position of that check's poll rows ordered by
database ID. Structured records are extracted only from a successful poll
with a retained response digest belonging to one of the three OCDS sources.
HTML-only observations never create empty or inferred procurement records.

## Stable identities and record files

For an OCDS release, use `release.ocid`, falling back to `release.id`. The
stable procurement ID is:

```text
record:<source_id>:<ocid-or-release-id>
```

No cross-source or fuzzy identity merge is allowed. A buyer or supplier with
an `id` uses that identifier; otherwise its name (or
`identifier.legalName`) is used. The entity ID is:

```text
entity:<role>:<source-identifier-or-name>
```

`role` is exactly `buyer` or `supplier`. The buyer is the release buyer, or
the first party with the buyer role. Suppliers are all award suppliers, or,
when no award supplier exists, all parties with the supplier role. Entity
`record_ids` are the sorted set of records in which that exact entity occurs.

Each stable record has exactly one public file named:

```text
records/<sha256(record_id)[:24]>.json
```

The file content is byte-for-byte the same canonical record object as the
entry in `records.json`. The expected record set is the union of all
structured records in eligible successful observations. The final record
object keeps the first and latest observed times, all eligible observation
IDs, the latest available projected fields, and the first time at which a
supported structured comparison changed it.

The structured record fields are deliberately narrow: release type and tag,
tender title, selected buyer and suppliers, consistent award/tender value,
consistent official dates, CPV classifications, consistent status, and the
first notice-document URL. Conflicting values are `null`; absent values are
not invented.

## Event semantics

The public `changes.json` stream contains source/evidence movement and
record-level movement for the five intelligence sources. It does not contain
control events. Control observations are the separate operational section.
The stream is generated only between consecutive successful observations that
have retained response content; failed polls do not create source or record
events and do not reset the previous successful projection.

For a source and current observation, events are:

| Type | Trigger | Comparison |
| --- | --- | --- |
| `raw_response_changed` | Current retained `content_sha256` differs from the previous successful retained digest | `mode=raw_response`, old/new digest |
| `content_changed` | Current normalisation row is marked changed and has a preceding normalisation digest | `mode=normalised_document`, old/new digest |
| `first_seen` | A record is present now and has not appeared in any earlier successful structured projection for this source | `mode=record`, new record snapshot |
| `value_changed` | Record `value` differs between the consecutive projections | `mode=structured_field`, `field=value`, old/new value |
| `date_changed` | Record `dates` differs between the consecutive projections | `mode=structured_field`, `field=dates`, old/new dates |
| `supplier_changed` | Record `suppliers` differs between the consecutive projections | `mode=structured_field`, `field=suppliers`, old/new suppliers |
| `status_changed` | Record `status` differs between the consecutive projections | `mode=structured_field`, `field=status`, old/new status |
| `disappeared` | A prior record is absent from the current projection | `mode=structured_record`, old record and `new=null`; this is not withdrawal evidence |

The event source is `target_id`, the record ID is the stable record ID when
the event is record-level, the current observation is `observation_id`, and
the previous observation is the immediately preceding successful retained
observation for that source. The timestamp is the current poll's
`polled_at`, i.e. first observed comparison time. It is never a publication,
award, contract-period, or publisher-side edit timestamp. The title is the
current record title for a present record and the prior title for a
disappearance. Source-level events have no record or title.

An event ID is `event:` followed by the first 24 hexadecimal characters of
SHA-256 over the canonical JSON event with its `id` member removed. Canonical
public JSON uses sorted keys, compact separators, JSON non-ASCII escaping,
and a final newline. The event object fields are compared exactly, including
the ID, event type, source/target ID, record ID, observation IDs, timestamp,
title, comparison mode, field, and old/new values.

Events within one observation are sorted by `(first_observed_at, id)` after
generation. The complete stream is sorted by the same key. This is the
canonical ordering and is part of the contract; duplicate IDs are invalid.

## Manifest and canonical ordering

Sources, entities, records, and per-source observation arrays are ordered by
their stable IDs or source poll order as specified above. `records.json` and
`entities.json` are sorted by ID. `changes.json` is sorted by event timestamp
and ID. Record evidence observation IDs are sorted by numeric poll ID.

`generated_at` is the latest `poll.polled_at` across all eligible checks. The
manifest summary is recomputed from the expected projection:

* `target_count` is the number of exported intelligence and control sources;
* `observation_count` is the number of eligible poll rows;
* `recorded_change_count` is the number of public events other than
  `first_seen`;
* `summary.government_sources`, `control_checks`, and
  `government_observations` count the corresponding source and observation
  sets;
* `summary.recorded_changes` excludes control events and excludes
  `first_seen` events; and
* timestamp coverage counts are counts of observation anchor statuses for
  intelligence sources only.

The manifest's chain heads are recomputed from the archive rows, never copied
from the mutable `anchor` table or from public objects. For each target the
head is the recomputed poll-chain head, the recomputed normalisation-chain
head (or the all-zero genesis value), and the recomputed global annotation
head. An observation's anchor status is valid only when its retained
manifest/proof digest, named chain heads, and poll coverage can be checked.
An `.ots` file alone never establishes Bitcoin backing.

The export ID is:

```text
export-<sha256(canonical({
  "schema_version": "1.0.0",
  "generated_at": generated_at,
  "files": files,
  "chain_heads": [source.chain_heads for source in sources]
}))[:32]>
```

`files` is the sorted list of every non-manifest JSON file in the public
export, each with its relative path, byte count, and SHA-256. The verifier
reconstructs this list and the canonical serialization independently. A
stale or fabricated ID therefore fails even if the manifest is internally
self-consistent.

The specification supports correspondence, completeness, and semantic
integrity as separate properties. It does not establish source truthfulness,
complete upstream polling, unsupported HTML-only semantics, human
interpretation choices beyond these rules, or Bitcoin anchoring solely from
the presence of a proof file.
