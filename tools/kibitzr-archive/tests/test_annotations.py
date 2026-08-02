"""The archive's ability to correct itself without rewriting itself.

The properties under test are the ones that make a correction worth more than
a tidy-up: an annotation must be appended rather than applied, it must be as
tamper-evident as the rows it describes, and it must not quietly widen to cover
rows it does not name.
"""
import json

import pytest

from kibitzr_archive.store import (GENESIS, ArchiveStore, compute_record_hash,
                                   fetch_id)


@pytest.fixture
def store(tmp_path):
    return ArchiveStore(str(tmp_path / "archive"))


# -- corrections leave the record alone -----------------------------------

def test_annotating_does_not_touch_the_rows_it_describes(store):
    """The whole point of addition 2: correct by appending, never by editing."""
    first = store.record_poll("check", ok=False, error="misleading cause")
    before = store.last_poll("check")["record_hash"]

    store.record_annotation(
        "correction",
        {"recorded": "misleading cause", "true_cause": "DNS was down"},
        check_name="check", subject_from=first.poll_id,
        subject_to=first.poll_id,
    )

    row = store.last_poll("check")
    assert row["error"] == "misleading cause"
    assert row["record_hash"] == before
    assert store.verify_chain("check") == (True, None)


def test_annotation_chain_detects_a_withdrawn_correction(store):
    """A correction that can be deleted silently is not a correction."""
    store.record_annotation("correction", {"true_cause": "DNS"},
                            check_name="check")
    store.record_annotation("note", {"text": "second"})
    assert store.verify_annotation_chain() == (True, None)

    with store._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM annotation WHERE id = 1")

    ok, bad = store.verify_annotation_chain()
    assert ok is False and bad == 2


def test_annotation_detail_cannot_be_edited_undetected(store):
    store.record_annotation("correction", {"true_cause": "DNS"},
                            check_name="check")

    with store._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE annotation SET detail = ? WHERE id = 1",
                     (json.dumps({"true_cause": "something else"}),))

    assert store.verify_annotation_chain() == (False, 1)


def test_effective_from_is_distinct_from_recorded_at(store):
    """A correction written today about yesterday must not claim we knew
    yesterday."""
    digest = store.record_annotation(
        "correction", {"true_cause": "DNS"},
        effective_from="2026-08-02T06:51:00+00:00",
    )
    row = store.annotations()[0]

    assert row["effective_from"] == "2026-08-02T06:51:00+00:00"
    assert row["recorded_at"] > row["effective_from"]
    assert row["record_hash"] == digest


def test_unknown_kinds_are_refused(store):
    with pytest.raises(ValueError):
        store.record_annotation("retraction", {})


def test_global_annotations_are_returned_for_every_check(store):
    store.record_annotation("fetch_regime", {"fetch_id": "abc"})
    store.record_annotation("note", {"text": "only this one"},
                            check_name="other")

    kinds = [a["kind"] for a in store.annotations(check_name="check")]
    assert kinds == ["fetch_regime"]


# -- fetch fingerprinting --------------------------------------------------

def test_fetch_id_moves_only_with_fetch_behaviour():
    base = {"url": "https://example.com"}
    assert fetch_id(base) == fetch_id(dict(base, transform=["text"]))
    assert fetch_id(base) != fetch_id(base, semantics=99)
    assert fetch_id(base) != fetch_id(dict(base, user_agent="Other/1.0"))


def test_fetch_id_is_recorded_and_hashed(store):
    store.record_poll("check", content="x", fetch_id_="abc123")

    row = store.last_poll("check")
    assert row["fetch_id"] == "abc123"
    assert store.verify_chain("check") == (True, None)

    with store._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE poll SET fetch_id = 'def456' WHERE id = 1")

    assert store.verify_chain("check") == (False, 1)


def test_rows_without_a_fetch_id_hash_exactly_as_before(store):
    """The upgrade must not invalidate an archive recorded before it.

    Reproduces the pre-upgrade payload literally rather than via the helper, so
    the test still fails if the helper is changed to start including the key.
    """
    record = store.record_poll("check", url="u", content="x",
                               polled_at="2026-08-01T00:00:00+00:00")
    legacy_fields = {
        "check": "check",
        "url": "u",
        "polled_at": "2026-08-01T00:00:00+00:00",
        "ok": True,
        "http_status": None,
        "content_sha256": record.content_sha256,
        "changed": True,
    }

    assert record.record_hash == compute_record_hash(legacy_fields, GENESIS)
    assert store.verify_chain("check") == (True, None)


def test_regime_change_is_visible_in_stats(store):
    store.record_poll("check", content="a", fetch_id_="regime-1")
    store.record_poll("check", ok=False, fetch_id_="regime-2")

    stats = store.stats("check")
    assert stats["fetch_revisions"] == 2
    assert stats["failures"] == 1


def test_the_pre_fingerprint_era_counts_as_a_regime(store):
    """The upgrade that introduced the fingerprint is itself a regime change,
    and is the one most worth flagging. Counting only non-null fingerprints
    would report it as a single unchanging regime."""
    store.record_poll("check", content="a")                      # before
    store.record_poll("check", content="b", fetch_id_="regime-2")  # after

    stats = store.stats("check")
    assert stats["unfingerprinted"] == 1
    assert stats["fetch_revisions"] == 2


def test_a_wholly_unfingerprinted_series_is_one_regime(store):
    store.record_poll("check", content="a")
    store.record_poll("check", content="b")

    assert store.stats("check")["fetch_revisions"] == 1


def test_regime_and_schedule_are_recorded_only_on_change(store):
    assert store.declare_fetch_regime("abc", 2, "first") is not None
    assert store.declare_fetch_regime("abc", 2, "first") is None
    assert store.declare_fetch_regime("def", 3, "changed") is not None

    assert store.declare_schedule("check", 21600) is not None
    assert store.declare_schedule("check", 21600) is None
    assert store.declare_schedule("check", 43200) is not None


# -- schedule as data ------------------------------------------------------

def _poll_at(store, when, check="check"):
    return store.record_poll(check, content=when, polled_at=when)


def test_a_gap_is_only_a_gap_against_declared_intent(store):
    """Addition 3: silence already means nobody looked. The schedule is what
    makes silence interpretable."""
    _poll_at(store, "2026-08-01T00:00:00+00:00")
    _poll_at(store, "2026-08-02T00:00:00+00:00")

    assert store.gaps("check") == []  # nothing declared, nothing judgeable

    store.declare_schedule("check", 21600,
                           effective_from="2026-07-31T00:00:00+00:00")
    gaps = store.gaps("check")

    assert len(gaps) == 1
    assert gaps[0]["seconds"] == 86400
    assert gaps[0]["period"] == 21600


def test_a_gap_is_judged_by_the_schedule_in_force_when_it_opened(store):
    store.declare_schedule("check", 86400,
                           effective_from="2026-07-01T00:00:00+00:00")
    _poll_at(store, "2026-07-02T00:00:00+00:00")
    _poll_at(store, "2026-07-03T00:00:00+00:00")
    # Tightened later; the interval above was collected under the old intent
    # and must not be retried against the new one.
    store.declare_schedule("check", 3600,
                           effective_from="2026-07-04T00:00:00+00:00")

    assert store.gaps("check") == []


def test_polling_at_the_declared_rate_produces_no_gap(store):
    store.declare_schedule("check", 21600,
                           effective_from="2026-07-31T00:00:00+00:00")
    _poll_at(store, "2026-08-01T00:00:00+00:00")
    _poll_at(store, "2026-08-01T06:00:00+00:00")

    assert store.gaps("check") == []


# -- anchoring -------------------------------------------------------------

def test_anchor_commits_to_the_annotation_chain(store):
    """An anchored correction must not be withdrawable."""
    store.record_poll("check", content="x")
    before = store.combined_head("check")

    store.record_annotation("correction", {"true_cause": "DNS"},
                            check_name="check")

    assert store.combined_head("check") != before


def test_anchor_is_reproducible_by_a_third_party(store):
    import hashlib

    store.record_poll("check", content="x")
    store.record_annotation("note", {"text": "hello"})

    payload = json.dumps({
        "poll": store.head("check"),
        "norm": GENESIS,
        "ann": store.annotation_head(),
        "v": 2,
    }, sort_keys=True, separators=(',', ':'))

    assert store.combined_head("check") == hashlib.sha256(
        payload.encode("utf-8")).hexdigest()
