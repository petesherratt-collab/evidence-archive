# Static publication architecture

The public evidence browser is a derived export. It is deliberately separated
from the collector and the evidential archive:

```text
Private collector and archive
    -> archive verification and explicit publication review
    -> reproducible, allow-listed static export
    -> public static host such as Vercel
```

Vercel, or any equivalent host, should serve only the generated report
directory. It must not run the collector, receive collector credentials, or
hold the mutable `polls.db`, private configuration, unreviewed retained
responses, or other evidential state. This repository does not currently
deploy to Vercel.

## Publication boundary

Publication must become an explicit allow-list operation before public hosting.
An expanded target catalogue should eventually give each target one of three
publication states: public, private, or redacted. Raw responses must not become
public automatically: they can contain copyrighted or personal material,
tokens reflected by a publisher, operational details, or content unrelated to
the selected document.

Stable target identifiers and URLs should be independent of mutable display
names. The current stable change-page identity is the global poll ID; display
names are content, not path components.

Four states must remain distinct:

- **Collected**: the collector recorded an observation.
- **Verified**: the archive chains and retained content passed verification.
- **Timestamped**: a proof covers the observation; pending calendar and
  Bitcoin-complete proofs remain distinct.
- **Published**: a reviewed derived representation was included in a static
  export.

One state does not imply another.

## Deployment provenance

Every export contains `publication-manifest.json`, which records the generator
version and commit, generation and verification time, verification result, and
aggregate publication counts. It describes the publication build only. It is
not an authoritative archive manifest and does not replace chain verification,
blob verification, or independent OpenTimestamps/Bitcoin verification.

A future deployment should additionally disclose the reviewed archive head or
heads that define its publication boundary. That value must come from the
verified archive; it must not be invented by the hosting layer.

HTTP deployments should add defensive response headers at the host. In
particular, CSP directives such as `frame-ancestors` must be delivered as an
HTTP response header; a meta CSP cannot enforce them. Hosting headers should
strengthen the offline report, not require weakening its existing meta policy.

## Scaling beyond the current report

A larger target list will eventually need target and date indexes, pagination,
search metadata, and incremental generation so one observation does not rebuild
the entire site. Those remain static-build concerns. They do not require a
database, API, authentication system, Next.js application, or collector inside
Vercel, and none is introduced by the current work.

Before any deployment, define the allow-list and redaction review, test the
complete static directory under the intended path prefix, configure response
headers, and verify that no private target names, exception details, raw
responses, paths, or credentials crossed the publication boundary.
