# Independent public-export verification

Run the verifier against an archive and the export directory it is claimed to
represent:

```sh
python3 deploy/verify_public_export_independently.py /path/to/archive /path/to/export
```

The verifier uses only the archive database, retained response blobs, archive
chain/anchor files, and the public JSON directory. It does not import
`public_export.py` or call exporter transformations. It independently derives
the eligible source and control IDs, every eligible observation, structured
record and record-file ID, entity and record relationships, source metadata,
chain heads, manifest summaries, export ID, and ordered event stream. The
complete projection specification is in
[`public-projection-spec.md`](public-projection-spec.md).

Three independent properties are reported together only when all three pass:

1. correspondence — present public claims point to archive-derived evidence;
2. completeness — every eligible public claim is present, with no unexpected
   replacement; and
3. semantic integrity — records and events, including timestamps, titles,
   prior observations, comparison values, and IDs, are reproduced from the
   archive.

The result does not establish source truthfulness, complete upstream polling,
unsupported HTML-only semantics, human interpretation choices beyond the
projection specification, or Bitcoin anchoring solely from an `.ots` file.
The verifier must reject an internally rehashed but truncated export and an
internally rehashed event with fabricated semantic content.
