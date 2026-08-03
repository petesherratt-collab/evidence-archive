"""``kibitzr archive`` subcommands, registered via the kibitzr.cli entry point.

    kibitzr archive status          per-check polls, raw vs document changes
    kibitzr archive verify          recompute all three hash chains
    kibitzr archive fsck            blobs and proofs, which verify cannot see
    kibitzr archive head CHECK      chain head, for submitting to a timestamp
    kibitzr archive annotations     assertions about the log, incl. corrections
    kibitzr archive annotate        append a correction; never edits a row
    kibitzr archive gaps            holes in a series, read against intent
    kibitzr archive anchor          commit the heads to an external timestamp
    kibitzr archive anchors         proofs taken, and what is not yet covered
    kibitzr archive anchor-upgrade  calendar attestation -> Bitcoin attestation
    kibitzr archive anchor-verify   check a proof still holds
    kibitzr archive calibration     measured lag between change and observation
"""
import json
import os
import sys

import click

from . import integrity
from .store import ANNOTATION_KINDS, ArchiveStore


DEFAULT_ROOT = "archive"


def _open(root):
    if not os.path.exists(os.path.join(root, ArchiveStore.DB_NAME)):
        raise click.ClickException(
            f"No archive at {root!r}. Run some checks first, "
            f"or pass --root."
        )
    return ArchiveStore(root)


def _check_names(store):
    return store.check_names()


def _human_bytes(count):
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f}{unit}" if unit == "B" else f"{count:.1f}{unit}"
        count /= 1024
    return f"{count:.1f}GB"


def _duration(seconds):
    """Render a second count the way a reader will want to compare it."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    if seconds < 90:
        return f"{sign}{seconds:.0f}s"
    if seconds < 5400:
        return f"{sign}{seconds / 60:.1f}m"
    return f"{sign}{seconds / 3600:.1f}h"


def extend_cli(group):
    """Attach the ``archive`` command group to kibitzr's CLI."""

    @group.group()
    def archive():
        """Inspect and verify the poll archive"""

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    def status(root):
        """Per-check polls, changes and last observation"""
        store = _open(root)
        names = _check_names(store)
        if not names:
            click.echo("Archive is empty.")
            return

        click.echo(f"{'check':<40} {'polls':>6} {'raw chg':>8} "
                   f"{'doc chg':>8} {'last poll':>21}")
        click.echo("-" * 86)
        all_stats = {name: store.stats(name) for name in names}
        for name in names:
            stats = all_stats[name]
            last = store.last_poll(name)
            doc = (str(stats["normalised_changes"])
                   if stats["normalised"] else "-")
            click.echo(
                f"{name[:40]:<40} {stats['polls']:>6} {stats['changes']:>8} "
                f"{doc:>8} {last['polled_at']:>21}"
            )

        overall = store.stats()
        click.echo("-" * 86)
        click.echo(
            f"{'total':<40} {overall['polls']:>6} {overall['changes']:>8} "
            f"{overall['normalised_changes']:>8}"
        )
        click.echo(
            f"\n{overall['blobs']} retained responses, "
            f"{_human_bytes(overall['bytes'])} on disk."
        )
        click.echo(
            "\nraw chg = the response bytes moved. doc chg = the content your\n"
            "transforms select moved. They differ, often by a lot: CSP nonces\n"
            "and ASP.NET viewstate change raw bytes on every single request."
        )

        # Controls first, and loudly. Everything else in this report is read by
        # someone who came looking; a stalled control has to reach someone who
        # did not, because the failure it detects is the silent one — a
        # selector matching nothing, a swallowed transform error, a cached
        # response — and every one of those looks exactly like a quiet target.
        controls = store.control_checks() & set(names)
        stalled = []
        for name in sorted(controls):
            consecutive, last_change_at, rows = store.normalisation_stall(name)
            if rows and consecutive >= store.CONTROL_STALL_THRESHOLD:
                stalled.append((name, consecutive, last_change_at))
        if stalled:
            click.echo("\n*** CONTROL STALLED — the pipeline may be broken ***")
            for name, consecutive, last_change_at in stalled:
                click.echo(
                    f"  {name}: {consecutive} consecutive polls with no "
                    f"document change"
                )
                click.echo(
                    f"    last change {last_change_at or 'never'}"
                )
            click.echo(
                "\nA control is a page that is KNOWN to change faster than it\n"
                "is polled, so its document changing on every poll is the\n"
                "working state. Until this clears, treat unchanged polls on\n"
                "every OTHER check as unverified rather than as null results."
            )
            # Two very different faults land here and the remedies do not
            # overlap, so the report has to separate them rather than leaving
            # the reader to guess which end to look at.
            click.echo(
                "\nWhich end is broken: run\n"
                f"  kibitzr archive calibration --check {stalled[0][0]!r}\n"
                "If the lag has grown past the page's own publishing interval,\n"
                "the PAGE stopped being rebuilt and the collector is fine. If\n"
                "the lag is normal but the document is not changing, the page\n"
                "is fresh and the COLLECTOR stopped seeing it — a selector\n"
                "matching nothing, a swallowed transform error, or a cache."
            )
        elif controls:
            # Deliberately weaker than it wants to be. All this command can see
            # is that the document moved between polls, and a human editing the
            # page by hand produces exactly those rows — which is how this
            # message read on the day the control was installed and its
            # scheduler had never once fired. Asserting corroboration from
            # inside the archive would be the same unearned inference the
            # control exists to prevent, so it reports the observation and
            # names what the reader still has to establish elsewhere.
            click.echo(
                f"\nControl checks moving: {', '.join(sorted(controls))}.\n"
                f"That is consistent with a working pipeline, and is only "
                f"evidence of\none if the page's changes are known to be "
                f"autonomous — check that its\npublishing schedule actually "
                f"ran, not merely that the content differs."
            )

        # Judge selectors on the normalised count. The raw count is inflated by
        # per-request churn on most real sites, so using it here — as this
        # command used to — reports broken selectors that are working fine.
        #
        # Controls are exempt: they change on every poll by construction, so
        # they would sit permanently in this list and train the reader to
        # ignore it.
        noisy = [
            name for name in names
            if name not in controls
            and all_stats[name]["normalised"] >= 10
            and (all_stats[name]["normalised_changes"]
                 > all_stats[name]["normalised"] * 0.5)
        ]
        if noisy:
            click.echo("\nSelected content changing on over half of all polls "
                       "— check selectors:")
            for name in noisy:
                click.echo(f"  - {name}")

        # The counterpart of the transform warning below, for the other half of
        # the pipeline. A change here does not alter a single collected byte,
        # but it does change when a poll counts as failed — so a shift in the
        # failure rate across it is the instrument moving, not the target.
        refetched = [name for name in names
                     if all_stats[name]["fetch_revisions"] > 1]
        if refetched:
            click.echo("\nFetch behaviour changed mid-series — failure counts "
                       "before and after are not comparable:")
            for name in refetched:
                click.echo(f"  - {name} "
                           f"({all_stats[name]['fetch_revisions']} regimes, "
                           f"{all_stats[name]['failures']} failed polls) "
                           f"— see `archive annotations --kind fetch_regime`")

        retuned = [name for name in names
                   if all_stats[name]["transform_revisions"] > 1]
        if retuned:
            click.echo("\nTransform rules changed mid-series — some diffs in "
                       "these are yours, not theirs:")
            for name in retuned:
                click.echo(f"  - {name} "
                           f"({all_stats[name]['transform_revisions']} "
                           f"rule sets)")

        unarchived = [name for name in names if not all_stats[name]["normalised"]]
        if unarchived:
            click.echo("\nNo normalised hashes recorded (check has no transform "
                       "chain, or ran before this feature existed):")
            for name in unarchived:
                click.echo(f"  - {name}")

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.argument("name", nargs=-1)
    def verify(root, name):
        """Recompute hash chains; exits non-zero if any is broken"""
        store = _open(root)
        names = list(name) or _check_names(store)
        if not names:
            click.echo("Archive is empty.")
            return

        broken = []
        checked = 0
        for check in names:
            for label, verify_chain in (
                ("poll", store.verify_chain),
                ("normalised", store.verify_normalisation_chain),
            ):
                ok, bad_id = verify_chain(check)
                checked += 1
                if ok:
                    click.echo(f"  ok      {check}  [{label}]")
                else:
                    click.echo(f"  BROKEN  {check}  [{label}]  "
                               f"(first bad row id {bad_id})")
                    broken.append(f"{check} [{label}]")

        # The annotation chain is global, so it is verified once rather than
        # per check. A withdrawn correction has to be as detectable as a
        # doctored poll, or appending corrections would be no safer than
        # editing rows.
        ok, bad_id = store.verify_annotation_chain()
        checked += 1
        if ok:
            click.echo("  ok      (all checks)  [annotations]")
        else:
            click.echo(f"  BROKEN  (all checks)  [annotations]  "
                       f"(first bad row id {bad_id})")
            broken.append("annotations")

        if broken:
            click.echo(
                f"\n{len(broken)} chain(s) failed verification. Either the log "
                f"was edited or rows were removed.",
                err=True,
            )
            sys.exit(1)
        click.echo(f"\nAll {checked} chain(s) intact.")
        click.echo(
            "This covers polls.db alone. Run `archive fsck` for the retained "
            "responses\nand the proofs, which no chain reaches."
        )

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--quiet", is_flag=True,
                  help="Print only findings and the verdict")
    def fsck(root, quiet):
        """Blobs and proofs are present and match — what verify cannot see"""
        store = _open(root)
        findings, counts = integrity.check(store)

        if not quiet:
            click.echo(
                f"{counts['blobs']} blob(s) on disk, "
                f"{counts['referenced']} referenced by the log; "
                f"{counts['anchors']} anchor(s) recorded."
            )

        for finding in findings:
            label = "BROKEN " if finding.severity == integrity.BROKEN else "note"
            click.echo(f"  {label:<7} {finding.kind}: {finding.detail}")

        damaged = integrity.broken(findings)
        if damaged:
            click.echo(
                f"\n{len(damaged)} integrity failure(s). This archive is "
                f"missing evidence it\nclaims to hold — do not treat it as a "
                f"good copy.",
                err=True,
            )
            sys.exit(1)

        # Said explicitly because the whole point of this command is that
        # "verify passed" was never the same statement as "nothing is missing".
        click.echo(
            f"\nEvery referenced response and every recorded proof is present "
            f"and matches."
        )
        if counts["exposed"]:
            click.echo(
                f"Sound, but {counts['exposed']} poll(s) are not yet covered "
                f"by a proof."
            )

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--kind", type=click.Choice(ANNOTATION_KINDS),
                  help="Only annotations of this kind")
    @click.argument("name", required=False)
    def annotations(root, kind, name):
        """Assertions about the log: corrections, regimes, declared schedules"""
        store = _open(root)
        rows = store.annotations(kind=kind, check_name=name)
        if not rows:
            click.echo("No annotations recorded.")
            return
        for row in rows:
            scope = row["check_name"] or "(all checks)"
            click.echo(f"#{row['id']}  {row['kind']}  {scope}")
            click.echo(f"    effective from {row['effective_from']}"
                       f"  (recorded {row['recorded_at']})")
            if row["subject_from"] is not None:
                click.echo(f"    concerns polls {row['subject_from']}"
                           f"..{row['subject_to']}")
            detail = json.dumps(row["detail"], indent=6, sort_keys=True)
            click.echo(f"    {detail.strip()}")
            click.echo()

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--kind", type=click.Choice(ANNOTATION_KINDS),
                  default="correction", help="Annotation kind")
    @click.option("--check", "check_name", help="Check the annotation is about")
    @click.option("--from-poll", type=int, help="First poll id concerned")
    @click.option("--to-poll", type=int, help="Last poll id concerned")
    @click.option("--effective-from", help="When the asserted fact became true "
                                           "(default: now)")
    @click.option("--detail", "detail_json", required=True,
                  help="JSON body of the assertion")
    def annotate(root, kind, check_name, from_poll, to_poll, effective_from,
                 detail_json):
        """Append an assertion about the log — never edits an existing row

        This is the sanctioned way to correct the archive. Poll rows are hashed
        into a chain precisely so they cannot be revised, and spending that
        property to tidy an embarrassing record would cost more than the record
        does. Corrections are appended and read alongside what they describe.
        """
        store = _open(root)
        try:
            detail = json.loads(detail_json)
        except ValueError as exc:
            raise click.ClickException(f"--detail is not valid JSON: {exc}")
        digest = store.record_annotation(
            kind, detail, check_name=check_name, subject_from=from_poll,
            subject_to=to_poll, effective_from=effective_from,
        )
        click.echo(f"Recorded {kind} annotation: {digest}")

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--tolerance", default=2.0, show_default=True,
                  help="Multiple of the declared period before a gap is one")
    @click.argument("name", nargs=-1)
    def gaps(root, tolerance, name):
        """Holes in a series, judged against the declared schedule

        Every poll writes a row, so silence already means nobody looked. What
        this adds is whether we *intended* to be looking: a hole is only a gap
        in coverage if a schedule was in force across it.
        """
        store = _open(root)
        names = list(name) or _check_names(store)
        found = 0
        unjudgeable = []
        for check in names:
            if not store.annotations("schedule", check_name=check):
                unjudgeable.append(check)
                continue
            for gap in store.gaps(check, tolerance=tolerance):
                found += 1
                hours = gap["seconds"] / 3600
                click.echo(
                    f"{check}\n"
                    f"    {gap['from']} -> {gap['to']}"
                    f"  ({hours:.1f}h, declared every "
                    f"{gap['period'] / 3600:.1f}h)\n"
                    f"    polls {gap['from_poll']} -> {gap['to_poll']}"
                )
        if unjudgeable:
            click.echo("\nNo schedule declared, so silence in these cannot be "
                       "read as a gap either way:")
            for check in unjudgeable:
                click.echo(f"  - {check}")
        if not found:
            click.echo("\nNo gaps beyond tolerance in the judgeable checks.")

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--ots", help="Path to the OpenTimestamps client")
    @click.argument("name", nargs=-1)
    def anchor(root, ots, name):
        """Commit the current chain heads to an external timestamp

        The chains prove the archive is internally consistent. They prove
        nothing about when it existed — a consistent history can be fabricated
        after the fact. This is the step that makes the difference, and the one
        whose delay is unrecoverable: time that passes unattested cannot be
        proven retroactively.
        """
        from .anchor import AnchorError, stamp  # noqa: PLC0415

        store = _open(root)
        names = list(name) or _check_names(store)
        try:
            result = stamp(store, names, ots=ots)
        except AnchorError as exc:
            raise click.ClickException(str(exc))

        click.echo(f"Manifest  {result['manifest_ref']}")
        click.echo(f"          sha256 {result['manifest_sha256']}")
        for check in result["checks"]:
            click.echo(f"  anchored  {check}")
        if result["status"] == "failed":
            click.echo(f"\nStamping failed: {result['output']}", err=True)
            click.echo("Recorded as failed rather than silently skipped.",
                       err=True)
            sys.exit(1)
        click.echo(
            "\nProof is PENDING: it currently rests on the calendar servers, "
            "not\non Bitcoin. Run `archive anchor-upgrade` in a few hours to "
            "convert it."
        )

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    def anchors(root):
        """Proofs taken, and how much is not yet covered by one"""
        store = _open(root)
        rows = store.anchors()
        if rows:
            click.echo(f"{'when':<21} {'status':<9} {'check':<34} {'head':>12}")
            click.echo("-" * 80)
            for row in rows:
                click.echo(
                    f"{row['anchored_at']:<21} {row['status']:<9} "
                    f"{row['check_name'][:34]:<34} "
                    f"{row['combined_head'][:12]:>12}"
                )
        else:
            click.echo("Nothing anchored yet.")

        click.echo("\nPolls not yet covered by any proof:")
        exposed = 0
        for check in _check_names(store):
            count = store.unanchored_polls(check)
            exposed += count
            click.echo(f"  {count:>4}  {check}")
        click.echo(f"  {exposed:>4}  total")
        if exposed:
            click.echo(
                "\nThese observations have no external evidence of when they "
                "existed.\nThat is recoverable only by anchoring before more "
                "time passes."
            )
        pending = [r for r in rows if r["status"] == "pending"]
        if pending:
            click.echo(
                f"\n{len(pending)} proof(s) still PENDING — resting on the "
                "calendar servers\nrather than Bitcoin. Run "
                "`archive anchor-upgrade`."
            )

    @archive.command(name="anchor-upgrade")
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--ots", help="Path to the OpenTimestamps client")
    def anchor_upgrade(root, ots):
        """Convert pending calendar attestations into Bitcoin attestations"""
        from .anchor import AnchorError, upgrade  # noqa: PLC0415

        store = _open(root)
        try:
            results = upgrade(store, ots=ots)
        except AnchorError as exc:
            raise click.ClickException(str(exc))
        if not results:
            click.echo("No pending proofs.")
            return
        for manifest_ref, status, output in results:
            click.echo(f"  {status:<9} {manifest_ref}")
            if status == "pending":
                click.echo(f"            {output.splitlines()[-1] if output else ''}")
        click.echo("\nPending proofs are normal for the first few hours: the "
                   "attestation\nappears once a Bitcoin block commits to it.")

    @archive.command(name="anchor-verify")
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--ots", help="Path to the OpenTimestamps client")
    @click.argument("manifest", required=False)
    def anchor_verify(root, ots, manifest):
        """Check a proof still holds, and that its manifest is unaltered"""
        from .anchor import AnchorError, verify  # noqa: PLC0415

        store = _open(root)
        refs = ([manifest] if manifest
                else sorted({row["manifest_ref"] for row in store.anchors()
                             if row["proof_ref"]}))
        if not refs:
            click.echo("Nothing anchored yet.")
            return
        failed = 0
        for ref in refs:
            try:
                result = verify(store, ref, ots=ots)
            except AnchorError as exc:
                raise click.ClickException(str(exc))
            if result["ok"]:
                click.echo(f"  ok      {ref}")
            else:
                failed += 1
                click.echo(f"  FAILED  {ref}")
                click.echo(f"          {result.get('reason') or result.get('output')}")
        if failed:
            sys.exit(1)

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--chain", type=click.Choice(["combined", "poll",
                                                "normalised"]),
                  default="combined",
                  help="Which chain head to print (default: combined)")
    @click.argument("name", required=False)
    def head(root, chain, name):
        """Print chain head(s) — the value to submit for timestamping

        There are two chains, and anchoring one of them would leave the other
        free to be rewritten, so the default commits to both.
        """
        store = _open(root)
        names = [name] if name else _check_names(store)
        getter = {
            "combined": store.combined_head,
            "poll": store.head,
            "normalised": store.normalisation_head,
        }[chain]
        for check in names:
            value = getter(check)
            if value is None:
                click.echo(f"{check}: nothing recorded on the {chain} chain",
                           err=True)
            elif len(names) == 1:
                click.echo(value)
            else:
                click.echo(f"{value}  {check}")

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.option("--check", "check_name", required=True,
                  help="Check to calibrate — normally a control")
    @click.option("--pattern", default=r'datetime="([^"]+)"',
                  help="Regex with one group capturing an ISO 8601 instant "
                       "in the retained response")
    def calibration(root, check_name, pattern):
        """Measured lag between a page changing and the archive seeing it

        The configured poll period is a floor on observation resolution, not
        the real figure. Scheduler drift, retries and fetch time all widen the
        bracket, and none of them are visible in the period. This reads the
        actual width off the record.

        It works by comparing when a page says it was generated against when
        the poll that retained it happened, which is only possible for a target
        that publishes its own generation time — in practice, the control. The
        answer does not transfer to other checks as a measurement, but it does
        as an order of magnitude: they run through the same scheduler.

        Why it matters: a claim of the form "this notice changed between X and
        Y" is exactly as strong as the bracket around it, and quoting the
        configured period there would overstate the archive's resolution.
        """
        import re  # noqa: PLC0415
        from datetime import datetime  # noqa: PLC0415

        store = _open(root)
        try:
            expression = re.compile(pattern)
        except re.error as exc:
            raise click.ClickException(f"--pattern is not a valid regex: {exc}")
        if expression.groups != 1:
            raise click.ClickException(
                f"--pattern must have exactly one capturing group, "
                f"got {expression.groups}")

        observations = store.observations(check_name)
        if not observations:
            raise click.ClickException(
                f"No retained responses for {check_name!r}. Calibration reads "
                f"the generation time back out of the stored body, so it needs "
                f"raw retention on the check.")

        lags, unmatched, unreadable = [], 0, 0
        for row in observations:
            try:
                body = store.get_blob(row["raw_ref"]).decode(
                    "utf-8", errors="replace")
            except OSError:
                unreadable += 1
                continue
            found = expression.search(body)
            if not found:
                unmatched += 1
                continue
            try:
                generated = datetime.fromisoformat(
                    found.group(1).replace("Z", "+00:00"))
                observed = datetime.fromisoformat(row["polled_at"])
            except ValueError:
                unmatched += 1
                continue
            lags.append(((observed - generated).total_seconds(),
                         row["polled_at"]))

        if not lags:
            raise click.ClickException(
                f"No generation time matched {pattern!r} in "
                f"{len(observations)} retained response(s). Check the pattern "
                f"against the stored body, not against the live page.")

        values = sorted(lag for lag, _ in lags)
        worst_lag, worst_at = max(lags)
        median = values[len(values) // 2]
        click.echo(f"{check_name}")
        click.echo(f"  {len(values)} of {len(observations)} retained responses "
                   f"carried a generation time")
        click.echo(f"  min    {_duration(values[0])}")
        click.echo(f"  median {_duration(median)}")
        click.echo(f"  max    {_duration(worst_lag)}   at {worst_at}")
        if unmatched:
            click.echo(f"  {unmatched} response(s) had no parseable match")
        if unreadable:
            click.echo(f"  {unreadable} blob(s) could not be read")

        negative = [value for value in values if value < 0]
        if negative:
            click.echo(
                "\nSome lags are NEGATIVE, meaning the page claims to have "
                "been\ngenerated after it was observed. That is a clock "
                "disagreement\nbetween the publisher and this machine, not a "
                "fast fetch, and it\nsets a floor on how tightly any bracket "
                "here can be stated."
            )
        click.echo(
            f"\nObservation resolution for this check is the poll period plus "
            f"the\nlag above — not the period alone. State brackets at "
            f"{_duration(worst_lag)} or wider\nunless you have a reason to "
            f"quote the median."
        )
