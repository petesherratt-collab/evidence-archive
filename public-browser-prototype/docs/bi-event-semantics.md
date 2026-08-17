# Public event semantics

The export contains two different kinds of activity. The exporter records
source-level evidence movement and record-level procurement movement for the
five intelligence targets in one `changes.json` stream. Control observations
remain available in their source/observation section, but control events do
not enter the BI event stream. The stream is therefore an event log, not a
count of procurements and not a count of publisher-side changes.

The public browser uses the existing exported target metadata as its explicit
classification boundary:

```text
business_intelligence_target = (source.kind == "source")
```

The control target has `kind == "control"`, so it is visible in the separate
operational section but cannot enter business-intelligence aggregates.

## Event types in this snapshot

| Event type | Meaning | Unit counted | Level |
| --- | --- | --- | --- |
| `first_seen` | A stable structured procurement record appeared in the deterministic source projection for the first time. | One stable exported record ID | Record-level event |
| `disappeared` | A stable record was absent from a later deterministic source projection. This is not evidence that a publisher withdrew or cancelled it. | One stable exported record ID | Record-level event |
| `value_changed` | The normalised value for a stable record differed from the previous observation. | One record-level field event | Field-level event |
| `date_changed` | The normalised dates for a stable record differed from the previous observation. | One record-level field event | Field-level event |
| `supplier_changed` | The normalised supplier relationship for a stable record differed from the previous observation. | One record-level field event | Field-level event |
| `status_changed` | The normalised status for a stable record differed from the previous observation. | One record-level field event | Field-level event |
| `raw_response_changed` | The retained response content digest differed from the prior successful observation. | One source observation comparison | Source-level event |
| `content_changed` | The normalised document digest differed from the prior successful observation. | One source observation comparison | Source-level event |

`recorded_change_count` in `sources.json` follows the exporter’s existing
definition: it counts public events except `first_seen`. Control observations
have no procurement record IDs and contribute no public BI events.

## Public terminology

The homepage calls the business-facing activity measure **record activity
events**. It counts only government-targeted events with a `record_id`, and
excludes `disappeared` because absence from a later projection is not a
procurement action. It includes `first_seen` and the supported field events,
which are shown with their exact deterministic event type. This avoids calling
collector polling or source-digest movement procurement activity.

The Activity page retains the complete deterministic event stream for
government sources. Source-level events remain labelled as source/evidence
events and do not receive a procurement record link. The control target is
shown through the separate operational source/observation section.

## Stable identities and limitations

Procurement records use the exporter’s stable `record:<source>:<ocid-or-id>`
identifier. Buyers and suppliers use the exporter’s stable `entity:<role>:`
identifier, derived from the source party identifier. Repeated observations
therefore do not increase procurement, buyer, or supplier counts.

The record identifier is source-scoped. If the same underlying OCDS release is
present in two separately monitored source projections, the public browser
does not fuzzy-merge those records. This is deliberate: the export does not
provide a cross-source canonical procurement identity.

## Value and category limits

Award values are aggregated only from the current exported snapshot, once per
stable record, and only where `amount` is a finite number and `currency` is a
known string. Currencies are kept separate; no conversion is performed and
null or malformed amounts are excluded. This snapshot has usable numeric
values for 1,125 of 1,888 records, with GBP, EUR, and USD totals. A QAR value
has a null amount and is not included. The homepage therefore shows separate
observed totals and coverage rather than a misleading single grand total.

CPV data is present for 1,077 of 1,888 records (57%). Because category
coverage is incomplete and the browser has no additional classification
ontology, category intelligence is documented but not promoted to a homepage
ranking in this pass.

Official award/publication dates are kept separate from `first_observed_at`.
The latter is when the collector saw the record; it is never used as a
substitute for an absent official date. Timestamp coverage is likewise kept
separate: pending OpenTimestamps evidence is not labelled Bitcoin-backed.
