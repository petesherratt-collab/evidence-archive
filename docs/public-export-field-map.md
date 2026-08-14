# Public export field map

This map records what the first public export can support from the archive as
it exists. It is deliberately conservative: a missing value is exported as
`null` (or omitted where the object is not applicable), and no classifier or
fuzzy matching is applied.

The archive boundary is the SQLite database at `archive/polls.db`, retained
response blobs under `archive/blobs/`, and the recorded chain/anchor files.
The exporter opens SQLite read-only and verifies retained bytes against the
`poll.content_sha256` value before using them.

## Target, source, and observation fields

| Public field | Source field / derivation | Confidence / status |
| --- | --- | --- |
| `source.id` | Stable slug derived from the configured `poll.check_name` | deterministic derivation |
| `source.name` | `poll.check_name` | directly stored |
| `source.url` | URL on the latest successful `poll` where available; configured URL is retained separately | directly stored / deterministic derivation |
| `source.source_type` | Existing target configuration: OCDS API or public HTML publication | deterministic derivation |
| `source.category` | Existing target configuration only: government contracts, frameworks, or government spending | deterministic derivation |
| `observation.id` | Stable `source-slug:poll:<poll.id>` | deterministic derivation |
| `observation.observed_at` | `poll.polled_at` | directly stored |
| `observation.result` | `poll.ok`, `http_status`, `content_length`, `etag`, `last_modified`, and `error` | directly stored |
| `observation.capture.content_sha256` | `poll.content_sha256` | directly stored |
| `observation.capture.previous_content_sha256` | Previous successful retained poll for the same check | deterministic derivation |
| `observation.capture.changed` | `poll.changed` | directly stored |
| `observation.normalisation` | Matching `normalisation` row: digest, transform ID, changed flag, and chain hash | directly stored |
| `observation.chain` | Recomputed poll chain fields and `poll.prev_hash` / `poll.record_hash` | deterministic derivation |
| `observation.anchor` | `anchor` row plus manifest and `.ots` proof digest validation | deterministic derivation |
| `manifest.per_target_chain_heads` | Recomputed poll, normalisation, and annotation chain heads | deterministic derivation |

The archive has six configured checks at discovery time. The `Control` check
is marked as `kind: control` and remains available for audit context, but is
kept separate from the procurement source table. The exporter can omit it with
`--exclude-control`.

## Procurement record fields

Structured records are derived only from retained JSON responses for the three
OCDS targets: Contracts Finder awards, Find a Tender awards, and the Contracts
Finder direct-awards view. GCA agreements and DHSC monthly spending currently
retain selected HTML/text output, not a structured procurement document, so
they do not produce fabricated records.

| Public field | Source field / derivation | Confidence / status |
| --- | --- | --- |
| `record.id` | `release.ocid` (or `release.id` fallback), namespaced by source slug | deterministic derivation |
| `record.type` | Release tags and presence of `awards`: `award` or `procurement` | deterministic derivation |
| `record.title` | `release.tender.title` | directly stored when supplied |
| `record.buyer` | `release.buyer`, or a `parties` entry with role `buyer` | directly stored when supplied |
| `record.supplier` / `record.suppliers` | `award.suppliers`, or `parties` entries with role `supplier` | directly stored when supplied |
| `record.notice_type` | First `release.tag` | directly stored when supplied |
| `record.value` | A consistent `award.value`, otherwise `tender.value` for releases without awards | deterministic derivation; `null` if conflicting or absent |
| `record.dates.published` | `release.publishedDate`, then award document `datePublished`, then `release.date` | deterministic derivation |
| `record.dates.award` | A consistent `award.date` | deterministic derivation; `null` if conflicting or absent |
| `record.dates.start` / `end` | A consistent `award.contractPeriod`, or `tender.contractPeriod` when no awards exist | deterministic derivation |
| `record.classification.cpv` | `tender.classification` and item `additionalClassifications` where `scheme` is `CPV` | directly stored / deterministic derivation |
| `record.classification.category` | A CPV description only when the selected CPV descriptions agree | deterministic derivation; otherwise `null` |
| `record.status` | A consistent `award.status`, or `tender.status` when no awards exist | directly stored when supplied; otherwise `null` |
| `record.source.notice_url` | First URL in award or release `documents` | directly stored when supplied |
| `record.dates.first_observed` | First successful retained observation containing the record | deterministic derivation |
| `record.dates.last_observed` | Latest successful retained observation containing the record | deterministic derivation |
| `record.dates.first_observed_changed` | First event time for a deterministic structured comparison (`value`, date, supplier, status, or disappearance) | deterministic derivation |
| `record.evidence.observation_ids` | Successful observations whose deterministic projection contained the record | deterministic derivation |

The current archive does not provide a universally safe mapping for buyer
legal type, supplier ownership, officer relationships, contract totals across
all source types, or a complete lifecycle state. Those are intentionally not
invented. The exporter does not convert absence from a later source response
into a claim that a notice was withdrawn; it emits `disappeared` with an
explicit interpretation note.

## Change and activity fields

| Public field | Source field / derivation | Confidence / status |
| --- | --- | --- |
| `change.first_observed_at` | Observation `poll.polled_at` at which the comparison first differed | directly stored / deterministic derivation |
| `change.event_type: first_seen` | First successful structured projection containing a record | deterministic derivation |
| `change.event_type: raw_response_changed` | Different successive successful `poll.content_sha256` values | deterministic derivation |
| `change.event_type: content_changed` | Different successive normalisation content hashes where `normalisation.changed` is true | deterministic derivation |
| `change.event_type: value_changed` | Deterministic comparison of exported record `value` objects | deterministic derivation |
| `change.event_type: date_changed` | Deterministic comparison of exported official date objects | deterministic derivation |
| `change.event_type: supplier_changed` | Deterministic comparison of ordered supplier objects | deterministic derivation |
| `change.event_type: status_changed` | Deterministic comparison of supplied status values | deterministic derivation |
| `change.event_type: disappeared` | Record present in one structured projection and absent in a later one | deterministic derivation; not evidence of withdrawal |
| `change.comparison.mode` | `raw_response`, `normalised_document`, or `structured_field` | deterministic derivation |

`observed_at`, official publication/award dates, and first-observed-change time
are separate fields throughout the export. The interface uses “first observed
changed” and never presents it as the publisher's change time.

## Timestamp coverage

| Public field | Source field / derivation | Confidence / status |
| --- | --- | --- |
| `anchor.status: none` | No valid retained anchor covers the observation | directly supported; awaiting coverage |
| `anchor.status: pending` | A retained proof and manifest validate, but the archive anchor status is pending | directly supported; not Bitcoin-backed |
| `anchor.status: bitcoin-backed` | A retained proof covers the manifest digest and the archive anchor status is complete | directly supported by retained archive state |
| `anchor.status: verification-failed` | An anchor row has retained proof metadata that cannot be validated | deterministic derivation |

An `.ots` file by itself is never promoted to `bitcoin-backed`. The current
export does not expose collector credentials, Backblaze credentials, live
archive access, proof-submission operations, or retained HTML as executable
browser content.

## Desired fields currently unavailable or deliberately excluded

| Desired BI field | Reason |
| --- | --- |
| Complete buyer legal type for every source | Not consistently stored in the selected archive projections |
| Contract value totals across GCA/DHSC and all source families | Those targets currently retain HTML/text captures without a deterministic contract parser |
| Corporate ownership or officer graphs | Explicitly outside the first relationship model |
| Universal lifecycle status (`planned`, `open`, `awarded`, `active`, `expired`, `cancelled/withdrawn`) | Source values are partial and absence is not a supported lifecycle claim |
| Publisher-side change time | The archive stores observation time and first observed comparison time, not the publisher's edit timestamp |
| Bitcoin block height/time for each observation | The archive's OTS metadata currently supports the conservative coverage states above, not a public block-attestation field |
| Public retained response bytes | The first export exposes hashes and provenance only; bytes remain outside the browser boundary |
