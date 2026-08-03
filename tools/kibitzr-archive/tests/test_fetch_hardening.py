"""The fetch path's limits, and the boundary it draws around a target.

These behaviours were ported out of a hardened working copy of kibitzr rather
than deployed as a fork, so that the collection stack stays something a third
party can reconstruct from public pieces. Each test names the untrue thing the
archive would otherwise be able to record.

Nothing here opens a socket. The point under test is what this code does with
what it is given, so DNS and the network are stubbed.
"""
import socket

import pytest
import requests

pytest.importorskip("kibitzr")

from kibitzr.exceptions import ConfigurationError  # noqa: E402

from kibitzr_archive import security  # noqa: E402
from kibitzr_archive.pinning import normalize_host, pinned_adapter  # noqa: E402
from kibitzr_archive.promoter import CapturingSessionFetcher  # noqa: E402


def addrinfo(*addresses):
    """Shape of `socket.getaddrinfo`'s return, as `resolve_target` reads it."""
    return [(None, None, None, None, (address, 0)) for address in addresses]


@pytest.fixture
def resolves(monkeypatch):
    """Point every lookup at whatever the test says it resolves to."""
    def _resolves(*addresses):
        monkeypatch.setattr(
            security.socket, "getaddrinfo",
            lambda *a, **k: addrinfo(*addresses))
    return _resolves


def fetcher(resolves, **conf):
    resolves("93.184.216.34")
    return CapturingSessionFetcher(
        dict({"name": "c", "url": "https://example.com/p"}, **conf))


# -- the cache ------------------------------------------------------------

def test_responses_are_not_cached(resolves):
    """The reason this override exists at all.

    kibitzr wraps the session in `CacheControl`. The fetcher is built once per
    check and lives as long as the process, so that cache spans polls: a target
    serving a long `max-age` would have polls answered without the origin being
    contacted, and those polls would enter the archive as observations
    indistinguishable from genuinely unchanged ones.
    """
    session = fetcher(resolves).session

    assert type(session).__name__ == "Session", (
        "session must be a plain requests.Session, not a CacheControl wrapper")
    assert not hasattr(session, "cache")
    assert session.headers["Cache-Control"] == "no-cache, no-store, max-age=0"


# -- the network boundary -------------------------------------------------

def test_private_targets_are_refused(resolves):
    resolves("127.0.0.1")

    with pytest.raises(ConfigurationError, match="private or non-public"):
        security.vet_url("http://localhost/x", {})


def test_private_targets_are_allowed_when_asked_for(resolves):
    resolves("127.0.0.1")

    url, addresses = security.vet_url(
        "http://localhost/x", {"allow_private_network": True})

    assert (url, addresses) == ("http://localhost/x", ["127.0.0.1"])


def test_a_name_that_does_not_resolve_stays_retriable(monkeypatch):
    """The distinction that produced 25 falsely attributed rows in this
    archive. DNS being down is a transient network condition; raising
    ConfigurationError for it would put it outside `SessionFetcher.EXCEPTED`,
    skipping the retry loop — and nothing above `Checker.check` catches it, so
    one blip would stop the whole daemon.
    """
    def boom(*args, **kwargs):
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(security.socket, "getaddrinfo", boom)

    with pytest.raises(requests.ConnectionError):
        security.vet_url("https://example.com/p", {})

    assert issubclass(requests.ConnectionError, CapturingSessionFetcher.EXCEPTED)


def test_cross_origin_redirects_are_refused(resolves):
    """The poll row records the configured URL, so a redirect landing on
    another origin would file someone else's content under this check's name.
    """
    resolves("93.184.216.34")

    with pytest.raises(ConfigurationError, match="cross-origin"):
        security.vet_url("https://elsewhere.example/p", {},
                         original_url="https://example.com/p")


def test_same_origin_redirects_are_followed(resolves):
    resolves("93.184.216.34")

    url, _ = security.vet_url("/moved", {},
                              original_url="https://example.com/p")

    assert url == "https://example.com/moved"


def test_non_http_schemes_are_refused():
    with pytest.raises(ConfigurationError, match="http or https"):
        security.vet_url("file:///etc/passwd", {})


# -- pinning ---------------------------------------------------------------

def test_unvetted_hosts_are_refused_rather_than_resolved():
    """Fails closed. Falling back to an ordinary lookup is the exact gap the
    adapter exists to close."""
    adapter = pinned_adapter({})
    connection_cls = adapter._pool_classes["https"].ConnectionCls
    connection = connection_cls.__new__(connection_cls)
    connection.host = "example.com"

    with pytest.raises(ConfigurationError, match="unvetted host"):
        connection._new_conn()


def test_proxied_requests_are_refused_by_default():
    """A proxy resolves the target itself, so pinning cannot cover that path.
    requests picks proxies up from the environment with nothing in the config
    mentioning them, so this must fail loudly rather than degrade."""
    adapter = pinned_adapter({}, allow_proxy=False)

    with pytest.raises(ConfigurationError, match="Refusing to fetch through"):
        adapter.proxy_manager_for("http://proxy.local:3128")


def test_hosts_are_keyed_the_way_dns_treats_them():
    assert normalize_host("EXAMPLE.com.") == "example.com"
    assert normalize_host("[::1]") == "::1"


def test_vetting_pins_what_it_validated(resolves):
    """Validation and connection must not be able to disagree about where a
    hostname points."""
    instance = fetcher(resolves)
    resolves("93.184.216.34", "93.184.216.35")

    instance._vet("https://example.com/p")

    assert instance.pins == {
        "example.com": ["93.184.216.34", "93.184.216.35"]}


# -- limits ----------------------------------------------------------------

class FakeResponse:
    """Just enough response for `_consume_response` and `_decode`."""

    def __init__(self, chunks, status_code=200, content_type="text/html"):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.closed = False

    def iter_content(self, _size):
        return iter(self._chunks)

    def close(self):
        self.closed = True


def test_oversized_responses_are_refused_mid_stream(resolves):
    """Refused while streaming, not after buffering — the archive retains every
    raw response, so an unbounded body is a disk ceiling as well as a memory
    one."""
    instance = fetcher(resolves, max_response_bytes=100)
    response = FakeResponse([b"x" * 60, b"x" * 60])

    with pytest.raises(requests.RequestException, match="max_response_bytes"):
        instance._consume_response(response, deadline=float("inf"))

    assert response.closed, "the connection is released even on refusal"


def test_the_deadline_bounds_a_trickling_response(resolves):
    """`timeout` is per-read-idle, never a total: a body arriving a byte at a
    time inside the read timeout never trips it."""
    instance = fetcher(resolves)
    response = FakeResponse([b"x", b"x", b"x"])

    with pytest.raises(requests.Timeout, match="Maximum fetch duration"):
        instance._consume_response(response, deadline=float("-inf"))


def test_per_hop_timeouts_are_clipped_to_the_deadline(resolves):
    from kibitzr_archive import promoter

    instance = fetcher(resolves)
    connect, read = instance._timeout(promoter.monotonic() + 1)

    assert connect <= 1 and read <= 1


def test_an_expired_deadline_raises_rather_than_connecting(resolves):
    from kibitzr_archive import promoter

    instance = fetcher(resolves)

    with pytest.raises(requests.Timeout):
        instance._timeout(promoter.monotonic() - 1)


def test_backoff_is_capped_by_the_remaining_budget(resolves, monkeypatch):
    """kibitzr's own backoff for Timeout is 60 * (retry + 1), and it runs
    checks on one thread, so an uncapped sleep stalls every other check."""
    instance = fetcher(resolves)
    slept = []
    monkeypatch.setattr("kibitzr_archive.promoter.sleep", slept.append)
    monkeypatch.setattr(instance, "RETRIABLE_EXCEPTIONS",
                        [(OSError, 300)], raising=False)

    instance.sleep_on_exception(OSError("boom"), retry=0, max_sleep=7)

    assert slept == [7]


def test_backoff_is_capped_even_without_a_budget(resolves, monkeypatch):
    instance = fetcher(resolves, max_retry_seconds=10)
    slept = []
    monkeypatch.setattr("kibitzr_archive.promoter.sleep", slept.append)
    monkeypatch.setattr(instance, "RETRIABLE_EXCEPTIONS",
                        [(OSError, 300)], raising=False)

    instance.sleep_on_exception(OSError("boom"), retry=0)

    assert slept == [10]


# -- decoding --------------------------------------------------------------

def test_an_unknown_charset_degrades_instead_of_failing(resolves):
    """Otherwise a page can make itself un-archivable by naming a codec that
    does not exist — a non-retriable failure, every poll, forever."""
    instance = fetcher(resolves)
    response = FakeResponse([], content_type="text/html; charset=nonsense-8")

    assert instance._decode(b"hello", response) == "hello"


def test_a_bytes_to_bytes_codec_degrades(resolves):
    """`codecs.lookup` accepts rot_13; `bytes.decode` then refuses it with
    LookupError rather than UnicodeDecodeError."""
    instance = fetcher(resolves)
    response = FakeResponse([], content_type="text/html; charset=rot_13")

    assert instance._decode(b"hello", response) == "hello"


def test_undecodable_bytes_are_replaced_not_raised(resolves):
    instance = fetcher(resolves)
    response = FakeResponse([], content_type="text/html; charset=utf-8")

    result = instance._decode(b"ok \xff\xfe", response)

    assert result.startswith("ok ")


def test_a_wrong_configured_encoding_is_loud(resolves):
    """The operator's encoding is authoritative: if it is wrong that is a
    config error, and unlike a response-supplied charset it should not be
    quietly worked around."""
    instance = fetcher(resolves, encoding="ascii")
    response = FakeResponse([])

    with pytest.raises(ConfigurationError, match="configured encoding"):
        instance._decode(b"caf\xc3\xa9", response)


def test_a_declared_charset_is_honoured(resolves):
    instance = fetcher(resolves)
    response = FakeResponse([], content_type="text/html; charset=iso-8859-1")

    assert instance._decode(b"caf\xe9", response) == "café"


# -- empty responses -------------------------------------------------------

def test_a_too_short_response_fails_without_archiving_a_message(resolves):
    """The content slot is what gets hashed and retained, so a diagnostic
    string here would be archived as though the page had changed to say it."""
    instance = fetcher(resolves, minimum_content_bytes=10)
    response = FakeResponse([b"tiny"])

    ok, content = instance._consume_response(response, float("inf"))

    assert (ok, content) == (False, "")


def test_a_normal_response_passes_through(resolves):
    instance = fetcher(resolves)
    response = FakeResponse([b"<html>", b"body</html>"])

    ok, content = instance._consume_response(response, float("inf"))

    assert (ok, content) == (True, "<html>body</html>")
