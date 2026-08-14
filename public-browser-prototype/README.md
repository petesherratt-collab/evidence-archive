# UK Government Evidence Monitor

This directory is a static, read-only browser for a versioned JSON snapshot.
It does not connect to SQLite, the live archive, Backblaze, OpenTimestamps, or
any collector service. Retained HTML is not copied into the public export and
is never executed by this application.

## Build the public snapshot

From the repository root, with the archive plugin environment installed:

```bash
PYTHONPATH=tools/kibitzr-archive ../.venv/bin/kibitzr archive export-public \
  --root /home/peter/evidence-collection/archive \
  --output public-browser-prototype/public/evidence
```

The command stages output beside the requested directory, validates every JSON
object and retained-content hash, then atomically replaces the previous public
snapshot only after validation succeeds. It never opens the archive through the
write-capable collector store.

The snapshot contains `manifest.json`, `sources.json`, `entities.json`,
`records.json`, `changes.json`, per-source observation files, and one file per
structured record. `generated_at` is derived from the latest retained
observation, so an unchanged archive produces the same bytes.

## Local browser

```bash
python3 -m http.server 8000 --directory public-browser-prototype
```

Open `http://localhost:8000/`. The UI is static and can be deployed from this
directory to Vercel with no runtime secrets.

## Evidence limits

The current archive stores structured OCDS payloads for Contracts Finder, Find a
Tender, and the direct-awards view. Those can populate buyer, supplier, title,
notice type, values, currency, dates, CPV, and source notice URLs when present.
The GCA agreements and DHSC spending targets currently retain HTML/text
captures only, so the exporter leaves procurement record fields unavailable.

The public model keeps observed time, first-observed change time, official
publication/award/contract dates, and timestamp coverage separate. A pending
OTS proof is displayed as pending; only completed archive status is exported as
Bitcoin-backed.
