import pytest

from kibitzr_archive.store import GENESIS, ArchiveStore, compute_record_hash


@pytest.fixture
def store(tmp_path):
    return ArchiveStore(str(tmp_path / "archive"))


def test_unchanged_polls_are_still_logged(store):
    """The whole point: absence of change must be distinguishable from
    absence of observation."""
    for _ in range(3):
        store.record_poll("check", content="same")

    assert store.stats("check")["polls"] == 3
    assert store.stats("check")["changes"] == 1  # only the first observation


def test_change_detected_on_differing_content(store):
    first = store.record_poll("check", content="before")
    second = store.record_poll("check", content="before")
    third = store.record_poll("check", content="after")

    assert first.changed is True
    assert second.changed is False
    assert third.changed is True
    assert first.content_sha256 != third.content_sha256


def test_raw_content_is_retained_and_recoverable(store):
    record = store.record_poll("check", content="<html>original</html>")

    assert store.get_blob(record.raw_ref) == b"<html>original</html>"


def test_blobs_are_deduplicated(store):
    """Content addressing means a flapping document costs storage once."""
    store.record_poll("check", content="a")
    store.record_poll("check", content="b")
    store.record_poll("check", content="a")

    assert store.stats("check")["polls"] == 3
    assert store.stats()["blobs"] == 2


def test_failed_fetch_is_logged_but_is_not_a_change(store):
    store.record_poll("check", content="stable")
    failure = store.record_poll("check", ok=False, content=None,
                                error="ConnectionError")
    after = store.record_poll("check", content="stable")

    assert failure.changed is False
    assert failure.content_sha256 is None
    # The outage must not read as the document changing and changing back.
    assert after.changed is False
    assert store.stats("check")["polls"] == 3
    assert store.stats("check")["changes"] == 1


def test_response_metadata_is_stored(store):
    store.record_poll("check", url="https://example.com", content="x",
                      http_status=200, etag='W/"abc"',
                      last_modified="Wed, 01 Jul 2026 10:00:00 GMT")
    row = store.last_poll("check")

    assert row["http_status"] == 200
    assert row["etag"] == 'W/"abc"'
    assert row["last_modified"] == "Wed, 01 Jul 2026 10:00:00 GMT"
    assert row["url"] == "https://example.com"


def test_chain_starts_at_genesis_and_links(store):
    store.record_poll("check", content="one")
    store.record_poll("check", content="two")

    with store._connect() as conn:  # noqa: SLF001 - inspecting stored chain
        rows = conn.execute(
            "SELECT * FROM poll WHERE check_name = ? ORDER BY id", ("check",)
        ).fetchall()

    assert rows[0]["prev_hash"] == GENESIS
    assert rows[1]["prev_hash"] == rows[0]["record_hash"]


def test_verify_chain_accepts_intact_log(store):
    for value in ("a", "a", "b", "c"):
        store.record_poll("check", content=value)

    assert store.verify_chain("check") == (True, None)


def test_verify_chain_detects_edited_row(store):
    store.record_poll("check", content="a")
    store.record_poll("check", content="b")
    store.record_poll("check", content="c")

    with store._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE poll SET polled_at = ? WHERE id = 2",
                     ("2020-01-01T00:00:00+00:00",))

    ok, bad_id = store.verify_chain("check")
    assert ok is False
    assert bad_id == 2


def test_verify_chain_detects_deleted_row(store):
    for value in ("a", "b", "c"):
        store.record_poll("check", content=value)

    with store._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM poll WHERE id = 2")

    ok, bad_id = store.verify_chain("check")
    assert ok is False
    assert bad_id == 3


def test_head_is_the_latest_record_hash(store):
    assert store.head("check") is None
    store.record_poll("check", content="a")
    last = store.record_poll("check", content="b")

    assert store.head("check") == last.record_hash


def test_checks_have_independent_chains(store):
    store.record_poll("alpha", content="a")
    store.record_poll("beta", content="b")

    assert store.verify_chain("alpha") == (True, None)
    assert store.verify_chain("beta") == (True, None)
    assert store.head("alpha") != store.head("beta")


def test_record_hash_is_reproducible_from_row(store):
    """A third party holding the row must be able to recompute the hash."""
    store.record_poll("check", url="https://example.com", content="x",
                      http_status=200)
    row = store.last_poll("check")

    recomputed = compute_record_hash({
        "check": row["check_name"],
        "url": row["url"],
        "polled_at": row["polled_at"],
        "ok": bool(row["ok"]),
        "http_status": row["http_status"],
        "content_sha256": row["content_sha256"],
        "changed": bool(row["changed"]),
    }, row["prev_hash"])

    assert recomputed == row["record_hash"]


def test_store_reopens_existing_archive(tmp_path):
    root = str(tmp_path / "archive")
    ArchiveStore(root).record_poll("check", content="a")
    reopened = ArchiveStore(root)
    reopened.record_poll("check", content="b")

    assert reopened.stats("check")["polls"] == 2
    assert reopened.verify_chain("check") == (True, None)


def test_bytes_content_is_accepted(store):
    """PDF and other binary targets arrive as bytes, not str."""
    record = store.record_poll("check", content=b"%PDF-1.7 binary")

    assert store.get_blob(record.raw_ref) == b"%PDF-1.7 binary"
