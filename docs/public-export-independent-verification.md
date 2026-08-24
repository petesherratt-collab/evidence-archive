# Independent public-export verification

Run the verifier against an archive and the export directory it is claimed to
represent:

```sh
python3 deploy/verify_public_export_independently.py /path/to/archive /path/to/export
```

This is current-state verification: the export must be the complete projection
of every eligible row currently present in the supplied archive. A checked-in
snapshot can instead be verified against a later append-only archive with the
explicit historical mode:

```sh
python3 deploy/verify_public_export_independently.py --historical \
  /path/to/archive /path/to/export
```

Historical mode derives its cut from the export; it has no `--cut-poll` or
`--ignore-after` trust path. It locates every manifest poll and normalisation
head, authenticates the shared annotation head, checks the exported
observation tail for each target, and requires the collector identity to agree
with the annotation state. Each named chain is rebuilt from genesis through
the head. The verifier then checks that every eligible target row through the
largest authenticated global poll ID is included, and validates later chain
rows as a subsequent append-only suffix. Only after those checks does it
reconstruct the projection from the historical prefixes.

Successful historical output reports `verification_mode`,
`authenticated_cut_poll`, `archive_latest_poll`, and
`later_polls_ignored_as_subsequent_history`. The ignored rows are excluded
from reconstruction, not from authentication of the append-only chain.

The verifier uses only the archive database, retained response blobs, archive
chain/anchor files, and the public JSON directory. It does not import
`public_export.py` or call exporter transformations. It independently derives
the eligible source and control IDs, every eligible observation, structured
record and record-file ID, entity and record relationships, source metadata,
chain heads, manifest summaries, export ID, and ordered event stream. The
complete projection specification is in
[`docs/public-projection-spec.md`](public-projection-spec.md).

Three independent properties are reported together only when all three pass:

1. correspondence — present public claims point to archive-derived evidence;
2. completeness — every eligible public claim is present, with no unexpected
   replacement; and
3. semantic integrity — records and events, including timestamps, titles,
   prior observations, comparison values, and IDs, are reproduced from the
   archive.

## Correspondence

The verifier checks that every public observation resolves to the correct
archive poll, including its source, sequence, timestamp, result metadata,
retained content digest, normalisation reference, and poll-chain fields. Every
retained blob used for the projection is decompressed and hashed independently.
Structured records are reconstructed from retained OCDS response bytes, and
their stable IDs, mapped fields, evidence links, and source notice links must
match. Independently rebuilt poll, normalisation, and annotation chain heads
are compared with the export.

## Completeness

The archive-derived eligible universe is compared by exact set and exact
content equality. This covers sources, observations, procurement records,
record files, buyer and supplier entities, entity `record_ids`, evidence
relationships, and public JSON file inventory. The manifest file hashes and
byte counts, source/observation/record/event counts, value coverage, totals by
currency, timestamp-coverage summaries, chain-head summaries, and
independently derived `export_id` are also recomputed. Missing, duplicated,
unexpected, or internally rehashed objects therefore fail verification.

## Semantic integrity

The verifier independently reproduces the ordered public activity stream. For
each event it checks the event ID, event type, source ID, record ID, current and
previous observation IDs, observation timestamp, title, comparison mode,
field, old value, new value, and canonical ordering. It also checks stable
record snapshots and buyer/supplier relationships against the same retained
evidence. Control observations remain independently recognized but control
events cannot enter procurement BI activity.

The verifier reports archive facts separately from deterministic projection
rules. Poll metadata, retained digests, normalisation rows, and chain values
are archive facts. Source-scoped IDs, record extraction, event classification,
relationships, aggregates, and coverage summaries are independently
implemented rules from the written projection specification.

## Three different claims

Current-state completeness means exact equality with the eligible archive state
now. Historical-state completeness means exact equality with a valid state
whose chain heads and observation tails are authenticated inside the supplied
append-only archive; later valid appends do not change that result. Neither
claim proves when the export was published. Publication at a particular time
needs an external commitment, such as suitable Git provenance, an independently
retained backup, or a verified OpenTimestamps/Bitcoin attestation.

## Not established

Passing all three properties does not establish:

- that an upstream source was truthful;
- that changes were not missed between polls, or that polling was continuous;
- structured meaning for HTML-only or otherwise unstructured sources;
- Bitcoin backing from `.ots` file presence—actual OpenTimestamps verification
  and attestation still require the separate OTS/Bitcoin process; or
- that the formal OCDS/public-projection interpretation choices are the only
  semantically desirable choices. They are the contract being tested, not
  external proof of that contract’s desirability.
- that historical verification is a time machine or an external timestamp
  proof. It proves a valid historical prefix/state, not publication at that
  time.

The result does not establish source truthfulness, complete upstream polling,
unsupported HTML-only semantics, human interpretation choices beyond the
projection specification, or Bitcoin anchoring solely from an `.ots` file.
The verifier must reject an internally rehashed but truncated export and an
internally rehashed event with fabricated semantic content.
