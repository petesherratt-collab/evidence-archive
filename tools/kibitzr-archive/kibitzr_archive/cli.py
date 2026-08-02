"""``kibitzr archive`` subcommands, registered via the kibitzr.cli entry point.

    kibitzr archive status          per-check polls, raw vs document changes
    kibitzr archive verify          recompute all three hash chains
    kibitzr archive head CHECK      chain head, for submitting to a timestamp
    kibitzr archive annotations     assertions about the log, incl. corrections
    kibitzr archive annotate        append a correction; never edits a row
    kibitzr archive gaps            holes in a series, read against intent
"""
import json
import os
import sqlite3
import sys

import click

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
    with store._connect() as conn:  # noqa: SLF001
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT check_name FROM poll ORDER BY check_name"
        ).fetchall()
    return [row[0] for row in rows]


def _human_bytes(count):
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f}{unit}" if unit == "B" else f"{count:.1f}{unit}"
        count /= 1024
    return f"{count:.1f}GB"


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

        # Judge selectors on the normalised count. The raw count is inflated by
        # per-request churn on most real sites, so using it here — as this
        # command used to — reports broken selectors that are working fine.
        noisy = [
            name for name in names
            if all_stats[name]["normalised"] >= 10
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
