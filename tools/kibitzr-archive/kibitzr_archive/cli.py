"""``kibitzr archive`` subcommands, registered via the kibitzr.cli entry point.

    kibitzr archive status          per-check polls, changes, last seen
    kibitzr archive verify          recompute every hash chain
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

        click.echo(f"{'check':<44} {'polls':>7} {'changes':>8} {'last poll':>21}")
        click.echo("-" * 82)
        for name in names:
            stats = store.stats(name)
            last = store.last_poll(name)
            click.echo(
                f"{name[:44]:<44} {stats['polls']:>7} {stats['changes']:>8} "
                f"{last['polled_at']:>21}"
            )

        overall = store.stats()
        click.echo("-" * 82)
        click.echo(
            f"{'total':<44} {overall['polls']:>7} {overall['changes']:>8}"
        )
        click.echo(
            f"\n{overall['blobs']} retained responses, "
            f"{_human_bytes(overall['bytes'])} on disk."
        )

        # Growth rate is the cheapest proxy for normalisation quality: a target
        # changing far more often than its document plausibly does has broken
        # selectors, and it is much cheaper to catch that here than downstream.
        noisy = [
            name for name in names
            if store.stats(name)["polls"] >= 10
            and store.stats(name)["changes"] > store.stats(name)["polls"] * 0.5
        ]
        if noisy:
            click.echo("\nChanging on over half of all polls — check selectors:")
            for name in noisy:
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
        for check in names:
            ok, bad_id = store.verify_chain(check)
            if ok:
                click.echo(f"  ok      {check}")
            else:
                click.echo(f"  BROKEN  {check}  (first bad row id {bad_id})")
                broken.append(check)

        if broken:
            click.echo(
                f"\n{len(broken)} chain(s) failed verification. Either the log "
                f"was edited or rows were removed.",
                err=True,
            )
            sys.exit(1)
        click.echo(f"\nAll {len(names)} chain(s) intact.")

    @archive.command()
    @click.option("--root", default=DEFAULT_ROOT, help="Archive root directory")
    @click.argument("name", required=False)
    def head(root, name):
        """Print chain head(s) — the value to submit for timestamping"""
        store = _open(root)
        names = [name] if name else _check_names(store)
        for check in names:
            value = store.head(check)
            if value is None:
                click.echo(f"{check}: no polls recorded", err=True)
            elif len(names) == 1:
                click.echo(value)
            else:
                click.echo(f"{value}  {check}")
