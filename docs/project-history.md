# Project history

## Gotchas / verifier trust-boundary lessons

The first independent public verifier established archive correspondence but
not complete projection equality. A fully rehashed export with all records and
changes removed, or with a truncated collection made internally consistent by
editing its manifest, could therefore be accepted. A genuine value-change
event with a fabricated timestamp, title, old value, or new value was likewise
accepted because event references and types were checked without reproducing
the claim.

The repair formalises the public projection, reconstructs all eligible
archive-derived sets and relationships, recomputes manifest identity and
chain heads, and independently reproduces the deterministic event stream.
Adversarial tests cover deletion, truncation, replacement, fabricated records,
manifest identity, chain heads, and forged or missing/extra/duplicated events.

The CSV boundary had a separate spreadsheet formula-injection issue: checking
only the first character missed values whose first significant character was
`=`, `+`, `-`, or `@` after whitespace or control characters. CSV output now
protects those values while leaving JSON and browser data unchanged, with
regressions for spaces, tabs, CR, LF, mixed prefixes, benign later operators,
quoting, commas, embedded newlines, UTF-8, and complete filtered exports.

There are at least three distinct properties:

1. correspondence — present exported claims point to real archive evidence;
2. completeness — all required eligible public claims are present; and
3. semantic integrity — claims/events about that evidence are independently
   reproduced.

A verifier should not claim full verification unless all required properties
are established.
