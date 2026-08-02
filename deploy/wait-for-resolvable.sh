#!/usr/bin/env bash
# Block until DNS answers, or until a deadline passes.
#
# Why this exists: the unit used to carry `After=network-online.target`, with a
# comment explaining that it stopped a burst of boot-time DNS failures being
# recorded as failed polls. It did not. `network-online.target` belongs to the
# *system* systemd manager; in the per-user manager that runs this service it is
# simply not-found, so the ordering silently did nothing. On 2 Aug 2026 the
# service started before the laptop's network came up and wrote 25 failed polls
# into the archive.
#
# A user unit cannot order itself against a system target, so it has to ask.
#
# Deliberately best-effort. On timeout this exits 0 and lets collection start
# anyway: a target that is genuinely unreachable should be recorded as an
# unreachable target, which is a true observation and the archive's actual job.
# Blocking collection until the network looks perfect would trade a truthful
# failed poll for no poll at all, and only the second is unrecoverable.
set -uo pipefail

HOST="${1:-www.contractsfinder.service.gov.uk}"
DEADLINE="${2:-120}"
INTERVAL=3

elapsed=0
while ! getent hosts "$HOST" >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$DEADLINE" ]; then
        echo "wait-for-resolvable: '$HOST' still unresolvable after ${DEADLINE}s;" \
             "starting anyway — failures from here are real observations." >&2
        exit 0
    fi
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
done

if [ "$elapsed" -gt 0 ]; then
    echo "wait-for-resolvable: '$HOST' resolved after ${elapsed}s." >&2
fi
exit 0
