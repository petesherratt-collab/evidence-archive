# UK Government Evidence Monitor — public browser prototype

This is a concrete evolution of the previous `Evidence archive dashboard.html` operator mock-up into a public, read-only evidence browser.

## What changed

- Keeps the restrained table/card visual language.
- Replaces operator-first `Healthy / Failures / FSCK` hierarchy with `Sources / Observations / Recorded changes / Timestamp coverage`.
- Separates the independent collector-liveness control from government-source counts.
- Makes `OBSERVED`, `CHANGED`, and `ANCHORED` distinct concepts in the UI.
- Does not claim exact change times where the old aggregate report did not provide them.
- Does not equate an OTS proof with a verified Bitcoin attestation.
- Includes prototype JSON so the UI is already consuming a public-export boundary rather than archive files directly.

## Prototype data

The values are translated from the 5 August 2026 dashboard mock-up and are explicitly labelled **not live**. They are useful for layout and schema work only.

## Run locally

From this directory:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000/`.

## Deploy to Vercel

Set the Vercel project Root Directory to `public-browser-prototype`. This directory is deliberately static and can be deployed as-is. No collector credentials, archive path, B2 key, OTS submission capability, database, or write API is present.

## Next production step

Write a deterministic exporter in the evidence-archive repository that emits, to a staging directory:

- `manifest.json`
- `targets.json`
- `targets/<id>/summary.json`
- `targets/<id>/observations.json`
- `targets/<id>/changes.json`

Validate the export and referenced hashes/heads before atomically promoting a snapshot for publication.
