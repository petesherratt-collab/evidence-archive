"""``kibitzr archive`` subcommands, registered via the kibitzr.cli entry point.

    kibitzr archive status          per-check polls, raw vs document changes
    kibitzr archive verify          recompute both hash chains
    kibitzr archive head CHECK      chain head, for submitting to a timestamp
"""
import os
import sqlite3
import sys

import click

from .store import ArchiveStore


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
