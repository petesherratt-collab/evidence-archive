"""End-to-end tests through kibitzr's own fetcher selection and a live server."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("kibitzr")

from kibitzr.fetcher.factory import fetcher_factory  # noqa: E402

import kibitzr_archive.promoter as promoter_module  # noqa: E402
from kibitzr_archive.promoter import ArchivePromoter  # noqa: E402


class _State:
    body = "<html>original</html>"
    status = 200
    etag = 'W/"v1"'
    last_agent = None


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
            _State.last_agent = self.headers.get("User-Agent")
            payload = _State.body.encode("utf-8")
            self.send_response(_State.status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("ETag", _State.etag)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point every check at a throwaway archive root."""
    monkeypatch.setattr(promoter_module, "_STORES", {})
    monkeypatch.setattr(promoter_module, "DEFAULT_ROOT", str(tmp_path / "archive"))
    _State.body = "<html>original</html>"
    _State.status = 200
    _State.etag = 'W/"v1"'
    _State.last_agent = None


def test_archive_promoter_is_selected_only_when_opted_in(server):
    archived = fetcher_factory({"name": "a", "url": server, "archive": True})
    plain = fetcher_factory({"name": "b", "url": server})

    assert isinstance(archived, ArchivePromoter)
    assert not isinstance(plain, ArchivePromoter)


def test_content_passes_through_unchanged(server):
    fetcher = fetcher_factory({"name": "check", "url": server, "archive": True})

    ok, content = fetcher.fetch()

    assert ok is True
    assert content == "<html>original</html>"


def test_every_poll_is_logged_and_only_real_changes_flagged(server):
    fetcher = fetcher_factory({"name": "check", "url": server, "archive": True})

    fetcher.fetch()
    fetcher.fetch()
    _State.body = "<html>amended</html>"
    fetcher.fetch()

    stats = fetcher.store.stats("check")
    assert stats["polls"] == 3
    assert stats["changes"] == 2  # first observation, then the amendment


def test_raw_response_is_retained_verbatim(server):
    fetcher = fetcher_factory({"name": "check", "url": server, "archive": True})
    fetcher.fetch()
    _State.body = "<html>amended</html>"
    fetcher.fetch()

    rows_before = fetcher.store.last_poll("check")
    assert fetcher.store.get_blob(rows_before["raw_ref"]) == b"<html>amended</html>"


def test_response_headers_are_captured(server):
    fetcher = fetcher_factory({"name": "check", "url": server, "archive": True})
    fetcher.fetch()

    row = fetcher.store.last_poll("check")
    assert row["http_status"] == 200
    assert row["etag"] == 'W/"v1"'
    assert row["url"] == server


def test_chain_verifies_after_live_polls(server):
    fetcher = fetcher_factory({"name": "check", "url": server, "archive": True})
    for body in ("one", "one", "two"):
        _State.body = body
        fetcher.fetch()

    assert fetcher.store.verify_chain("check") == (True, None)
    assert fetcher.store.head("check") is not None


def test_failed_fetch_is_recorded_as_an_attempt(server):
    fetcher = fetcher_factory({"name": "check", "url": server, "archive": True})
    fetcher.fetch()

    fetcher.conf["url"] = "http://127.0.0.1:1/unreachable"
    fetcher._session_fetcher = None  # noqa: SLF001 - force reconnect to bad host
    with pytest.raises(Exception):
        fetcher.fetch()

    stats = fetcher.store.stats("check")
    assert stats["polls"] == 2  # the outage is visible, not silent
    row = fetcher.store.last_poll("check")
    assert row["ok"] == 0
    assert row["error"]
    assert fetcher.store.verify_chain("check") == (True, None)


def test_archived_fetch_identifies_itself(server):
    """The default agent names the project and gives a way to reach it.

    An archive that claims it collected openly has to have said who it was
    at fetch time; that cannot be added to the record afterwards.
    """
    fetcher_factory({"name": "a", "url": server, "archive": True}).fetch()

    assert _State.last_agent == promoter_module.DEFAULT_USER_AGENT
    assert "Kibitzr/" not in _State.last_agent


def test_user_agent_can_be_overridden_per_check(server):
    conf = {"name": "a", "url": server, "archive": True,
            "user_agent": "Example/2.0 (+mailto:someone@example.org)"}
    fetcher_factory(conf).fetch()

    assert _State.last_agent == "Example/2.0 (+mailto:someone@example.org)"


def test_user_agent_false_falls_back_to_kibitzr(server):
    """Opting out is available but has to be asked for by name."""
    conf = {"name": "a", "url": server, "archive": True, "user_agent": False}
    fetcher_factory(conf).fetch()

    assert _State.last_agent.startswith("Kibitzr/")
