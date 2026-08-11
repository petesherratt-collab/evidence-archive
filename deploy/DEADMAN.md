# Off-host dead-man receiver

This receiver belongs on infrastructure independent of the collector and of
`evidence-control`. It accepts authenticated events at `/events`, records the
most recent `collector-heartbeat`, and sends an alarm when that heartbeat is
older than 40 minutes. An `alert-delivery-failed` event alarms immediately.

Install `deadman-receiver.py` and the three `evidence-deadman-*` units on the
off-host machine. Put reviewed values from `deadman-receiver.env.example` in
`/etc/evidence-deadman/environment`; put an independent random bearer token in
the configured token file, mode 600. Terminate public HTTPS in front of the
receiver and expose only `POST /events`. Set `EVIDENCE_DEADMAN_URL` on the
collector to that HTTPS URL and install the same token in its mode-600 token
file. Neither credential may grant access to any evidence repository.

`DEADMAN_ALARM_URL` must be an alarm destination monitored independently of
both machines. Its webhook receives JSON with `text` and `event` fields. A
failed alarm request makes the checker or receiver unit fail, so the off-host
platform must also monitor those units.

Before production, send normal heartbeats, confirm the state timestamp moves,
then stop only the heartbeat timer long enough to cross the 40-minute threshold.
Confirm a human receives the absence alarm. Also point `EVIDENCE_ALERT_URL` at
a deliberate failure and confirm the receiver produces the alert-delivery
alarm. Restore configuration, remove any catch-up assumptions, and record the
test time and recipients in the production checklist. Configuration alone is
not proof that either alarm path works.
