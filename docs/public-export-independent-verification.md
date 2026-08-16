# Independent public-export verification

The deterministic archive is authoritative; the public browser is a read-only
projection. `deploy/verify_public_export_independently.py` therefore reads
`polls.db`, retained blobs, and the published JSON directly. It does not import
`kibitzr_archive.public_export` or any exporter transformation helper.

## Independently checked claims

- Every file named by `manifest.json` exists and has the stated byte length and
  SHA-256 digest. The manifest and aggregate files use supported schema version
  `1.0.0`; unexpected or unlisted JSON files are rejected.
- Each exported observation identifies an existing poll, and its source name,
  observed time, result fields, retained digest, chain hashes, and normalisation
  reference agree with the database. Retained gzip content is read and hashed
  independently against the poll digest.
- Structured OCDS records are reconstructed from retained JSON for the three
  currently declared structured source types. Stable source-scoped record IDs,
  title, buyer, suppliers, value/currency, dates, status, CPV values, and source
  notice URL are compared with the latest retained occurrence. This is a
  separate standard-library implementation of the documented field map.
- Record evidence observation IDs must exist and must be observations whose
  reconstructed retained source contains that record. Entity IDs and roles must
  resolve; entity `record_ids` must exactly reproduce record relationships.
- Activity record IDs and observations must resolve, event types are allow-
  listed, and record-level BI events may not refer to a control source. Source
  polling events remain evidence activity and are not counted as procurement
  activity.
- Procurement records, buyers, suppliers, award-value coverage, award totals by
  currency, and record-activity windows (24h, 7d, 30d) are recomputed from the
  checked export. Currencies are never converted or combined. Manifest source,
  observation, and change counts are independently recomputed and compared.

Any mismatch is printed and causes a non-zero exit status. The verifier opens
SQLite read-only and never modifies archive or export bytes.

## Direct facts, deterministic transforms, and limits

Poll times, URLs, success states, HTTP metadata, content digests, normalisation
rows, and chain hashes are direct archive facts. OCDS release fields are direct
retained-source facts. Source-scoped record/entity IDs, current-record choice,
event classifications, relationship lists, coverage summaries, and aggregate
counts are deterministic transforms.

Independence is bounded. The verifier independently implements the published
OCDS mapping, but agreement between two implementations is not proof that the
mapping is the only reasonable interpretation of OCDS. HTML-only sources do not
currently yield structured records. The verifier checks recorded anchor/proof
references and archive bindings exposed by observations, but it does not run
OpenTimestamps network/Bitcoin verification; `ots verify` remains separate.
It also cannot establish that a source was truthful, that polling occurred when
no poll exists, or that an official date represents when the archive observed a
change. Headline aggregates not stored in the export are reported rather than
compared to an exporter-authored total.
