import sqlite3

import pytest

from kibitzr_archive.hook import capture_index, install, rule_name, transform_rules
from kibitzr_archive.store import GENESIS, ArchiveStore, transform_id


@pytest.fixture
def store(tmp_path):
    return ArchiveStore(str(tmp_path / "archive"))


# -- the case this feature exists for ------------------------------------

def test_raw_churn_does_not_read_as_document_change(store):
    """A CSP nonce moves the raw bytes on every request while the selected
    content sits still. Before this feature the log called that a change."""
    for nonce in ("a1", "b2", "c3"):
        store.record_poll("check", content=f"<script nonce={nonce}>x</script>"
                                           f"<main>same</main>")
        store.record_normalisation("check", "same", transform_conf=["text"])

    stats = store.stats("check")
    assert stats["polls"] == 3
    assert stats["changes"] == 3          # raw moved every time
    assert stats["normalised"] == 3
    assert stats["normalised_changes"] == 1   # the document did not


def test_document_change_is_recorded_when_content_really_moves(store):
    first = store.record_normalisation("c", "before", transform_conf=["text"])
    same = store.record_normalisation("c", "before", transform_conf=["text"])
    moved = store.record_normalisation("c", "after", transform_conf=["text"])

    assert first.changed is True
    assert same.changed is False
    assert moved.changed is True
    assert first.content_sha256 != moved.content_sha256


def test_normalisation_links_to_the_poll_it_came_from(store):
    poll = store.record_poll("c", content="<main>x</main>")
    record = store.record_normalisation("c", "x", transform_conf=["text"])

    assert poll.poll_id is not None
    assert record.poll_id == poll.poll_id


def test_no_blob_is_written_for_normalised_content(store):
    """The raw response is already retained and the transform is fingerprinted,
    so the normalised form is re-derivable and need not be stored twice."""
    store.record_poll("c", content="<main>hello</main>")
    before = store.stats()["blobs"]
    store.record_normalisation("c", "hello", transform_conf=["text"])

    assert store.stats()["blobs"] == before


# -- retuning a selector must be distinguishable from the document moving --

def test_transform_fingerprint_changes_when_rules_change(store):
    first = store.record_normalisation("c", "x", transform_conf=[{"css": "main"}])
    retuned = store.record_normalisation("c", "x",
                                         transform_conf=[{"css": "#contents"}])

    assert first.transform_id != retuned.transform_id
    assert store.stats("c")["transform_revisions"] == 2


def test_transform_fingerprint_is_stable_for_equal_rules():
    assert transform_id([{"css": "main"}, "text"]) == \
           transform_id([{"css": "main"}, "text"])
    assert transform_id([]) == transform_id(None)


# -- chain integrity ------------------------------------------------------

def test_normalisation_chain_verifies_when_untouched(store):
    for value in ("a", "b", "c"):
        store.record_normalisation("c", value, transform_conf=["text"])

    assert store.verify_normalisation_chain("c") == (True, None)


def test_editing_a_normalised_hash_breaks_its_chain(store):
    for value in ("a", "b", "c"):
        store.record_normalisation("c", value, transform_conf=["text"])

    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE normalisation SET content_sha256 = ? WHERE id = 2",
                 ("0" * 64,))
    conn.commit()
    conn.close()

    ok, bad_id = store.verify_normalisation_chain("c")
    assert ok is False
    assert bad_id == 2


def test_editing_the_transform_fingerprint_breaks_the_chain(store):
    """Otherwise the keeper could retune a selector and deny having done so."""
    for value in ("a", "b"):
        store.record_normalisation("c", value, transform_conf=["text"])

    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE normalisation SET transform_id = ? WHERE id = 1",
                 ("f" * 64,))
    conn.commit()
    conn.close()

    assert store.verify_normalisation_chain("c")[0] is False


def test_deleting_a_normalisation_row_breaks_the_chain(store):
    for value in ("a", "b", "c"):
        store.record_normalisation("c", value, transform_conf=["text"])

    conn = sqlite3.connect(store.db_path)
    conn.execute("DELETE FROM normalisation WHERE id = 2")
    conn.commit()
    conn.close()

    assert store.verify_normalisation_chain("c")[0] is False


def test_poll_chain_still_verifies_alongside_normalisation(store):
    """Adding the second chain must not disturb the first."""
    store.record_poll("c", content="one")
    store.record_normalisation("c", "one", transform_conf=["text"])
    store.record_poll("c", content="two")
    store.record_normalisation("c", "two", transform_conf=["text"])

    assert store.verify_chain("c") == (True, None)
    assert store.verify_normalisation_chain("c") == (True, None)


# -- anchoring ------------------------------------------------------------

def test_combined_head_covers_both_chains(store):
    store.record_poll("c", content="one")
    store.record_normalisation("c", "one", transform_conf=["text"])
    before = store.combined_head("c")

    store.record_normalisation("c", "two", transform_conf=["text"])

    assert store.combined_head("c") != before, \
        "a normalised observation must move the anchor"


def test_combined_head_moves_when_only_the_poll_chain_moves(store):
    store.record_poll("c", content="one")
    store.record_normalisation("c", "one", transform_conf=["text"])
    before = store.combined_head("c")

    store.record_poll("c", content="two")

    assert store.combined_head("c") != before


def test_combined_head_is_none_without_polls(store):
    assert store.combined_head("nothing-here") is None


def test_combined_head_tolerates_an_empty_normalisation_chain(store):
    """A check with no transforms records raw polls only, and must still
    produce an anchorable value."""
    store.record_poll("c", content="one")

    assert store.normalisation_head("c") is None
    assert store.combined_head("c") is not None


# -- schema upgrade -------------------------------------------------------

def test_existing_poll_chains_survive_the_upgrade(tmp_path):
    """The chain version is pinned separately from the schema version, so
    adding the normalisation table must not invalidate recorded polls."""
    root = str(tmp_path / "archive")
    first = ArchiveStore(root)
    first.record_poll("c", content="one")
    first.record_poll("c", content="two")
    head_before = first.head("c")

    reopened = ArchiveStore(root)  # re-runs _ensure_schema

    assert reopened.verify_chain("c") == (True, None)
    assert reopened.head("c") == head_before


# -- where the capture goes in the pipeline -------------------------------

def test_capture_index_is_the_first_reporting_transform():
    rules = [{"css": "#contents"}, "text", {"changes": "verbose"}]
    assert capture_index(rules) == 2


def test_capture_index_is_end_of_pipeline_without_a_reporting_transform():
    rules = [{"css": "main"}, "text"]
    assert capture_index(rules) == 2


def test_capture_index_takes_the_first_of_several_reporting_transforms():
    rules = ["text", {"changes": "verbose"}, {"changes": "word"}]
    assert capture_index(rules) == 1


def test_rule_name_handles_both_rule_forms():
    assert rule_name("text") == "text"
    assert rule_name({"css": "main"}) == "css"


def test_transform_rules_accepts_a_bare_string():
    assert transform_rules({"transform": "text"}) == ["text"]
    assert transform_rules({}) == []


# -- the wrapper, against a stand-in pipeline ------------------------------

class FakePipeline:
    """Mimics kibitzr's TransformPipeline closely enough to wrap."""

    def __init__(self, transforms):
        self.transforms = transforms

    def run_pipeline(self, ok, content):
        for transform in self.transforms:
            if not ok:
                break
            ok, content = transform(content)
        return ok, content

    __call__ = run_pipeline


class FakeChecker:
    def __init__(self, conf, pipeline):
        self.conf = conf
        self.transform = pipeline


def test_wrapper_hashes_the_document_not_the_diff(store):
    """The regression that motivated the whole feature: hashing the end of a
    pipeline that ends in `changes` hashes an empty diff, so an unchanged poll
    and a changed-back poll would record the same value."""
    conf = {"name": "c", "archive": True,
            "transform": [{"css": "main"}, {"changes": "verbose"}]}
    select = lambda content: (True, "DOCUMENT")          # noqa: E731
    diff = lambda content: (True, "")                    # noqa: E731 - empty diff
    checker = FakeChecker(conf, FakePipeline([select, diff]))

    assert install(checker, store) is True
    ok, report = checker.transform(True, "<html>raw</html>")

    assert (ok, report) == (True, "")     # pipeline output is untouched
    row = store.stats("c")
    assert row["normalised"] == 1
    # what was hashed is the document, not the empty diff
    from kibitzr_archive.store import sha256_hex
    with sqlite3.connect(store.db_path) as conn:
        stored = conn.execute(
            "SELECT content_sha256 FROM normalisation").fetchone()[0]
    assert stored == sha256_hex(b"DOCUMENT")


def test_wrapper_captures_final_output_without_a_reporting_transform(store):
    conf = {"name": "c", "archive": True, "transform": ["text"]}
    checker = FakeChecker(conf, FakePipeline([lambda c: (True, "FINAL")]))

    assert install(checker, store) is True
    ok, report = checker.transform(True, "raw")

    assert (ok, report) == (True, "FINAL")
    assert store.stats("c")["normalised"] == 1


def test_wrapper_records_nothing_when_the_fetch_failed(store):
    conf = {"name": "c", "archive": True, "transform": ["text"]}
    checker = FakeChecker(conf, FakePipeline([lambda c: (True, "FINAL")]))
    install(checker, store)

    checker.transform(False, None)

    assert store.stats("c")["normalised"] == 0


def test_wrapper_never_breaks_the_check_it_observes(store, monkeypatch):
    conf = {"name": "c", "archive": True,
            "transform": [{"css": "main"}, {"changes": "verbose"}]}
    select = lambda content: (True, "DOCUMENT")          # noqa: E731
    diff = lambda content: (True, "a diff")              # noqa: E731
    checker = FakeChecker(conf, FakePipeline([select, diff]))
    install(checker, store)

    def explode(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "record_normalisation", explode)

    assert checker.transform(True, "raw") == (True, "a diff")


def test_install_declines_an_uninspectable_pipeline(store):
    class Opaque:
        def __call__(self, ok, content):
            return ok, content

    checker = FakeChecker({"name": "c", "transform": ["text"]}, Opaque())

    assert install(checker, store) is False
