# OpenTimestamps resiliency audit

The installed OpenTimestamps 0.7.2 CLI accepts repeated `--calendar` options
and, by default, considers stamping complete after two calendars reply. The
project does not override calendar URLs, so calendar selection, multi-calendar
submission, and proof aggregation remain client-owned. No third-party calendar
endpoint is hard-coded here.

`anchor.py` bounds stamp, upgrade, and verify subprocesses at 180 seconds. A
successful stamp with a proof is recorded as `pending`; only a later successful
upgrade without a pending response becomes `complete`. A stamp attempt that
produces no proof appends an `anchor_attempt_failed` annotation and removes the
unattested manifest rather than indexing a false anchor. It does not remove
polls, retained bytes, or chain history.

Runtime retry exists at two levels. The daily anchor service uses
`Restart=on-failure` after five minutes, and the pending-proof upgrade timer is
hourly, persistent, and also retries failed service runs after five minutes.
The DNS preflight is bounded and does not turn network unavailability into a
collector failure. Tests stub external calendars and cover pending-versus-
complete state, failed stamping, and later upgrade; they do not live-test
calendar availability.

Recommendation: retain the client defaults and current retries unless measured
failures demonstrate a gap. The invariant is already correct: an OTS outage
means awaiting timestamp coverage or a recorded failed attempt, never archive
corruption, collector-data deletion, or evidence loss. A future resilience
change should test CLI-version-specific behaviour rather than add guessed
calendar endpoints.
