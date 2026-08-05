"""The HTML report is a safe, portable view of the archive."""

import click
from click.testing import CliRunner

from kibitzr_archive.cli import extend_cli
from kibitzr_archive.store import ArchiveStore


def _cli():
    @click.group()
    def root():
        pass

    extend_cli(root)
    return root


def test_report_writes_a_self_contained_escaped_dashboard(tmp_path):
    archive = tmp_path / "archive"
    store = ArchiveStore(str(archive))
    store.record_poll("Check <unsafe>", url="https://example.invalid",
                      content=b"first")
    store.record_normalisation("Check <unsafe>", b"selected")
    output = tmp_path / "dashboard.html"

    result = CliRunner().invoke(_cli(), [
        "archive", "report", "--root", str(archive),
        "--output", str(output),
    ])

    assert result.exit_code == 0, result.output
    html = output.read_text(encoding="utf-8")
    assert "Evidence archive dashboard" in html
    assert "Check &lt;unsafe&gt;" in html
    assert "Check <unsafe>" not in html
    assert "Unanchored polls" in html
    assert "https://" not in html  # no external assets or requests


def test_recent_polls_are_newest_first_and_bounded(tmp_path):
    store = ArchiveStore(str(tmp_path / "archive"))
    for value in range(4):
        store.record_poll("check", content=str(value))

    rows = store.recent_polls(limit=2)

    assert [row["id"] for row in rows] == [4, 3]
