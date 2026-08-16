# Proposed improvements

This repository does not currently contain `docs/project-history.md`. The items
below are forward-looking project notes, not reconstructed history.

## PROPOSED / NOT YET IMPLEMENTED

- Keep extending independent public-export verification as new source formats
  or fields are added.
- Consider a dedicated, separately tested SQLite concurrency change after the
  concurrency audit; do not enable WAL as an incidental UI/export change.
- Revisit OpenTimestamps retry/calendar resilience only with evidence of a
  current gap and without turning timestamp unavailability into archive loss.
- Prometheus/OpenMetrics remains deferred for the current single collector.
- A future deterministic nonce diagnostic may report observed attribute
  variation while normalised content remains unchanged; it must not infer a
  cause such as tracking, honeypotting, or malicious behaviour.
- Keep the canonical developer-test command documented and revisit root pytest
  configuration only if package layout or installation practice changes.
