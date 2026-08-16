# Deterministic public-export fixture

`test_ci_fixture_is_deterministic_and_independently_verified` constructs this
fixture in a temporary directory so SQLite and gzip metadata are exercised
without committing generated databases or blobs.

Its literal inputs contain two targets (one OCDS procurement source and one
declared control), buyer and supplier identifiers, a GBP value, null optional
fields, two observations of one stable record, a deterministic value change,
`first_seen`, and a normalised control observation. The test exports the same
archive twice, compares every JSON path and SHA-256 hash, then runs the
standalone verifier. Expected target/control counts are asserted explicitly;
the other record/entity/event/hash expectations are asserted by the exporter
and independent-verifier tests.
