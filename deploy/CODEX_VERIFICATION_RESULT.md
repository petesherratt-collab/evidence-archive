# Evidence Archive verification result

Captured on 2026-08-18 from branch
`codex/integrated-public-browser`.

The work was based on integration commit:

`0bc0abcf2dc8de3e2f608900a109f7ebceb07688`

Authenticated historical-cut verification was subsequently committed as:

`6ffa7b29841cf82649dbb5ffe2e375fb69103e97`

Commit message:

`Add authenticated historical-cut verification`

That commit has been pushed to GitHub on
`codex/integrated-public-browser`.


## Archive and snapshot

Live archive:

`/home/peter/evidence-collection/archive`

Archive identity at capture, `polls.db` SHA-256:

`dc6189a8e328728550ea7a0efe7b7bfa132234a915579a3c24d3e56dfb70d908`

Live archive state at capture:

- 444 polls
- 401 normalisation rows
- 39 annotation rows
- 129 recorded anchors

Checked-in historical snapshot export ID:

`export-adc53ae73a759cd128df9575be0cb759`

Snapshot generated at:

`2026-08-14T06:32:38+00:00`

The checked-in historical snapshot contains:

- 288 observations
- 1,888 procurement records
- 2,099 recorded changes under its original publication semantics

Of those 2,099 recorded changes, 141 are legacy source-level control events:

- 70 `raw_response_changed`
- 71 `content_changed`

Those 141 events have no procurement `record_id`.

They are part of the semantics of the authenticated historical publication,
but they remain excluded from current procurement BI activity semantics.


## Authenticated historical cut

The historical verifier derives the cut from the snapshot's independently
validated `per_target_chain_heads`.

It does not accept an arbitrary poll number and does not provide an
`--ignore-after` escape hatch.

The authenticated snapshot heads resolve inside the later append-only live
archive to these poll heads:

- Contracts Finder: 283
- control: 288
- Departmental spend: 273
- Find a Tender: 284
- GCA: 272
- direct awards: 274

Their normalisation heads resolve to:

- 241
- 246
- 231
- 242
- 230
- 232

All share annotation head:

- 39

The live archive continues beyond those authenticated heads through poll 444.


## Historical verification result

Historical verification passed against the later live archive.

Command:

```sh
python3 deploy/verify_public_export_independently.py --historical \
  /home/peter/evidence-collection/archive \
  public-browser-prototype/public/evidence
