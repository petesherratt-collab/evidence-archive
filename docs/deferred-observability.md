# Deferred observability and diagnostics

Prometheus/OpenMetrics is deferred: the current single-collector environment
does not by itself justify another operational stack. If a concrete consumer
and alerting requirement appears, deterministic candidates include
`evidence_seconds_since_last_poll`, `evidence_unanchored_polls_count`,
`evidence_bitcoin_backed_proofs_count`, and `evidence_chain_length_total`.

A future source-variation diagnostic may count facts such as “attribute X
varied in N of M retained observations while normalised record content remained
unchanged.” Existing raw and normalisation digests support that shape of report.
It must keep measurement separate from interpretation and must not label the
cause tracking, honeypotting, anti-bot behaviour, or malice without independent
evidence.
