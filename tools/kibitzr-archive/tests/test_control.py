"""Control checks: tagging, stall detection, and observation-lag calibration.

A control is a page known to change faster than it is polled. Its value is
entirely in the inversion it creates: for every other check a long run of
unchanged polls is the finding, and for this one it is the alarm. These tests
pin that inversion down, because getting it backwards would produce an archive
that looks healthiest exactly when it has stopped working.
"""
import gzip

import click
import pytest
from click.testing import CliRunner

from kibitzr_archive.cli import extend_cli
from kibitzr_archive.store import ArchiveStore


@pytest.fixture
def store(tmp_path):
    return ArchiveStore(str(tmp_path / "archive"))


@pytest.fixture
def cli():
    @click.group()
    def root():
        pass

    extend_cli(root)
    return root


def _run(cli, root, *args):
    return CliRunner().invoke(cli, ["archive", *args, "--root", root])


# -- tagging -------------------------------------------------------------

def test_a_control_is_declared_on_the_chain_not_only_in_config(store):
    """A reader holding the archive without the config still has to be able
    to tell an instrument from a target."""
    assert store.declare_control("ctl") is not None

    assert store.control_checks() == {"ctl"}
    annotation = store.annotations(kind="note", check_name="ctl")[0]
    assert annotation["detail"]["role"] == "control"


def test_declaring_a_control_twice_appends_nothing(store):
    """kibitzr re-declares intent on every start, so this runs on each
    restart. The annotation chain is a log of regime changes, not of
    restarts."""
    store.declare_control("ctl")

    assert store.declare_control("ctl") is None
    assert len(store.annotations(kind="note", check_name="ctl")) == 1


def test_extra_detail_rides_along_with_the_role(store):
    store.declare_control("ctl", {"source": "https://example.invalid/"})

    detail = store.annotations(kind="note", check_name="ctl")[0]["detail"]
    assert detail["role"] == "control"
    assert detail["source"] == "https://example.invalid/"


def test_an_ordinary_note_does_not_make_a_check_a_control(store):
    """`note` is a general-purpose kind. Only the role field means control,
    or every future comment on a check would silently arm this alarm."""
    store.record_annotation("note", {"text": "selector retuned"},
                            check_name="target")

    assert store.control_checks() == set()


# -- stall detection -----------------------------------------------------

def test_a_ticking_control_does_not_stall(store):
    for value in ("a", "b", "c"):
        poll = store.record_poll("ctl", content=value)
        store.record_normalisation("ctl", value, poll_id=poll.poll_id)

    consecutive, last_change_at, rows = store.normalisation_stall("ctl")
    assert consecutive == 0
    assert last_change_at is not None
    assert rows == 3


def test_a_stalled_control_counts_back_to_the_last_change(store):
    """The count is of consecutive unchanged rows at the TAIL. A check that
    changed, went quiet, then changed again is not stalled now."""
    for value in ("a", "b", "c", "c", "c"):
        poll = store.record_poll("ctl", content=value)
        store.record_normalisation("ctl", value, poll_id=poll.poll_id)

    consecutive, _, _ = store.normalisation_stall("ctl")
    assert consecutive == 2


def test_a_stall_that_recovers_reads_as_zero(store):
    for value in ("a", "a", "a", "b"):
        poll = store.record_poll("ctl", content=value)
        store.record_normalisation("ctl", value, poll_id=poll.poll_id)

    consecutive, _, _ = store.normalisation_stall("ctl")
    assert consecutive == 0


def test_status_shouts_when_a_control_stalls(store, cli, tmp_path):
    store.declare_control("ctl")
    for value in ("a", "b", "c", "c", "c"):
        poll = store.record_poll("ctl", content=value)
        store.record_normalisation("ctl", value, poll_id=poll.poll_id)

    result = _run(cli, store.root, "status")

    assert "CONTROL STALLED" in result.output
    assert "2 consecutive polls" in result.output
    # The reader has to be told which end to look at; the two faults have
    # nothing in common but their symptom.
    assert "calibration" in result.output


def test_status_is_quiet_while_the_control_ticks(store, cli):
    store.declare_control("ctl")
    for value in ("a", "b", "c"):
        poll = store.record_poll("ctl", content=value)
        store.record_normalisation("ctl", value, poll_id=poll.poll_id)

    result = _run(cli, store.root, "status")

    assert "CONTROL STALLED" not in result.output
    assert "Control checks moving" in result.output
    # Not "corroborated". A human editing the page produces these same rows,
    # so the archive cannot license that inference from inside itself.
    assert "corroborat" not in result.output
    assert "known to be autonomous" in result.output


def test_a_stalled_ordinary_check_is_not_an_alarm(store, cli):
    """The same series without the control tag is an unremarkable quiet
    target — which is the entire distinction being drawn."""
    for value in ("a", "b", "b", "b"):
        poll = store.record_poll("target", content=value)
        store.record_normalisation("target", value, poll_id=poll.poll_id)

    result = _run(cli, store.root, "status")

    assert "CONTROL STALLED" not in result.output


def test_a_control_is_exempt_from_the_loose_selector_warning(store, cli):
    """It changes on every poll by construction. Listing it permanently
    would train the reader to ignore the warning that matters."""
    store.declare_control("ctl")
    for index in range(12):
        poll = store.record_poll("ctl", content=str(index))
        store.record_normalisation("ctl", str(index), poll_id=poll.poll_id)

    result = _run(cli, store.root, "status")

    assert "check selectors" not in result.output


# -- calibration ---------------------------------------------------------

def _retain(store, check, polled_at, body):
    """Record a poll with a retained response, as the promoter would.

    Both `content_sha256` and `raw_ref` are written, because that is what
    `record_poll` does and the two are always the same digest. Setting only
    `raw_ref` made this fixture the one shape the real writer never emits — an
    unattested reference with no attested digest beside it — which is exactly
    the shape the substituted-blob forgery took.
    """
    digest = store.put_blob(body.encode("utf-8"))
    with store._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO poll (check_name, polled_at, ok, changed,"
            " content_sha256, raw_ref, prev_hash, record_hash)"
            " VALUES (?,?,1,1,?,?,'x','y')",
            (check, polled_at, digest, digest),
        )


def test_calibration_measures_the_lag_it_can_see(store, cli):
    _retain(store, "ctl", "2026-08-03T12:00:30+00:00",
            '<time datetime="2026-08-03T12:00:00Z">x</time>')
    _retain(store, "ctl", "2026-08-03T13:04:00+00:00",
            '<time datetime="2026-08-03T13:00:00Z">x</time>')

    result = _run(cli, store.root, "calibration", "--check", "ctl")

    assert result.exit_code == 0
    assert "2 of 2 retained responses" in result.output
    assert "min    30s" in result.output
    assert "max    4.0m" in result.output


def test_calibration_flags_a_clock_disagreement(store, cli):
    """A page generated 'after' it was fetched is two clocks disagreeing, and
    it bounds how tightly any bracket can honestly be stated."""
    _retain(store, "ctl", "2026-08-03T12:00:00+00:00",
            '<time datetime="2026-08-03T12:00:30Z">x</time>')

    result = _run(cli, store.root, "calibration", "--check", "ctl")

    assert "NEGATIVE" in result.output


def test_calibration_refuses_a_pattern_that_captures_nothing(store, cli):
    _retain(store, "ctl", "2026-08-03T12:00:30+00:00", "<p>no timestamp</p>")

    result = _run(cli, store.root, "calibration", "--check", "ctl")

    assert result.exit_code != 0
    assert "No generation time matched" in result.output


def test_calibration_needs_retained_bodies(store, cli):
    store.record_poll("ctl", ok=False, error="boom")

    result = _run(cli, store.root, "calibration", "--check", "ctl")

    assert result.exit_code != 0
    assert "raw retention" in result.output


def test_calibration_rejects_a_multi_group_pattern(store, cli):
    _retain(store, "ctl", "2026-08-03T12:00:30+00:00", "x")

    result = _run(cli, store.root, "calibration", "--check", "ctl",
                  "--pattern", r"(a)(b)")

    assert result.exit_code != 0
    assert "exactly one capturing group" in result.output


def test_calibration_survives_an_unreadable_blob(store, cli, tmp_path):
    """Retention is on disk and disks lose things. One bad blob must not take
    the measurement with it."""
    _retain(store, "ctl", "2026-08-03T12:00:30+00:00",
            '<time datetime="2026-08-03T12:00:00Z">x</time>')
    _retain(store, "ctl", "2026-08-03T13:00:30+00:00",
            '<time datetime="2026-08-03T13:00:00Z">y</time>')
    victim = store.observations("ctl")[1]["raw_ref"]
    with open(store.blob_path(victim), "wb") as handle:
        handle.write(b"not gzip")

    result = _run(cli, store.root, "calibration", "--check", "ctl")

    assert result.exit_code == 0
    assert "1 of 2 retained responses" in result.output


def test_calibration_ignores_a_substituted_response(store, cli):
    """A blob that no longer hashes to the digest its poll attests to is not
    calibration data. Silently measuring it would let substituted bytes move a
    published number, which is a smaller harm than a forged archive but the
    same defect."""
    _retain(store, "ctl", "2026-08-03T12:00:30+00:00",
            '<time datetime="2026-08-03T12:00:00Z">x</time>')
    _retain(store, "ctl", "2026-08-03T13:00:30+00:00",
            '<time datetime="2026-08-03T13:00:00Z">y</time>')
    victim = store.observations("ctl")[1]["content_sha256"]
    with gzip.open(store.blob_path(victim), "wb") as handle:
        # Well-formed gzip, decodes cleanly, wrong bytes — the case that gets
        # through anything checking only that the file opens.
        handle.write(b'<time datetime="2026-08-03T09:00:00Z">forged</time>')

    result = _run(cli, store.root, "calibration", "--check", "ctl")

    assert result.exit_code == 0
    assert "1 of 2 retained responses" in result.output


def test_observations_skip_polls_that_retained_nothing(store):
    store.record_poll("ctl", content="kept")
    store.record_poll("ctl", ok=False, error="boom")

    assert len(store.observations("ctl")) == 1
    assert len(store.observations("ctl", only_with_raw=False)) == 2


def test_a_gzip_blob_round_trips_through_observations(store):
    poll = store.record_poll("ctl", content="body text")
    row = store.observations("ctl")[0]

    assert row["id"] == poll.poll_id
    with gzip.open(store.blob_path(row["raw_ref"]), "rb") as handle:
        assert handle.read() == b"body text"
