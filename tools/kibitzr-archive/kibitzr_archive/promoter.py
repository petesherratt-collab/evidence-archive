"""Kibitzr fetcher plugin that logs every poll and retains raw responses.

Registered through the ``kibitzr.fetcher`` entry point, so it installs
alongside an existing kibitzr rather than forking it. It wraps the normal
fetch, records the observation, and hands the content on unchanged — the
rest of the pipeline, including the ``changes`` transform and its git
history, behaves exactly as before.

Enable per check::

    checks:
      - name: Example Usage Policy
        url: https://example.com/policy
        archive: true              # or: archive: {root: ./archive}
        transform:
          - css: main
          - changes
"""
import codecs
import collections.abc
import logging
import re
from time import monotonic, sleep
from urllib.parse import urljoin, urlsplit

import requests
from kibitzr.exceptions import ConfigurationError
from kibitzr.fetcher.loader import URLPromoter
from kibitzr.fetcher.simple import SessionFetcher

from .pinning import normalize_host, pinned_adapter
from .security import vet_url
from .store import (FETCH_SEMANTICS_VERSION, ArchiveStore, fetch_id)


logger = logging.getLogger(__name__)

DEFAULT_ROOT = "archive"

#: A response bigger than this is refused rather than buffered. The archive
#: retains every raw response, so an unbounded body is both a memory ceiling
#: and a disk ceiling.
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
#: Wall-clock ceiling on one whole fetch, redirects included.
DEFAULT_MAX_FETCH_SECONDS = 60
#: Ceiling on time spent sleeping between retries of one fetch.
DEFAULT_MAX_RETRY_SECONDS = 90
CHUNK_SIZE = 64 * 1024
CONNECT_TIMEOUT = 3.05
READ_TIMEOUT = 27
# Bounded: the charset token comes off a response header, and there is no
# reason for a codec name to be long. Keeps an unbounded remote-supplied string
# out of the codec registry lookup.
RE_CHARSET = re.compile(r'charset\s*=\s*["\']?([^;"\'\s]{1,64})', re.IGNORECASE)

#: Sent on every archived fetch. An archive whose standing rests on having
#: collected openly has to say who is collecting; kibitzr's own fetcher
#: hardcodes ``Kibitzr/<version>``, which identifies the tool but not the
#: operator or a way to reach them. Override per check or globally with
#: ``user_agent``.
DEFAULT_USER_AGENT = (
    "EvidenceArchive/0.1 "
    "(+https://github.com/petesherratt-collab/evidence-archive)"
)

_STORES = {}


def check_fetch_id(conf):
    """Fetch-regime fingerprint for a check, derived one way only.

    The promoter records this on every poll and the start-up hook records it in
    the annotation naming the regime. If the two derived it separately they
    would eventually disagree, and the archive would carry an annotation
    describing a regime that no poll row claims to have been fetched under.

    Whether a browser is driven is kibitzr's decision, not ours, so it is taken
    from kibitzr's own predicate rather than re-implemented.
    """
    return fetch_id(dict(conf, firefox=URLPromoter.needs_firefox(conf)))


def causal_chain(exc, limit=8):
    """Render an exception together with what actually caused it.

    ``repr(exc)`` names only the exception that surfaced, which is the wrong
    one whenever a failure occurs *while handling* another failure. That is not
    a corner case here: a DNS outage raises ``socket.gaierror``, and any bug in
    the retry handler then replaces it with the handler's own exception. The
    poll log would go on to attribute the outage to whatever the handler
    tripped over, which is a false statement about the target's availability
    entered into an archive whose whole value is not making those.

    ``__cause__`` (explicit ``raise ... from``) and ``__context__`` (implicit,
    "this happened while handling that") are both followed, the second marked
    as such so the two are not conflated. Bounded, because a chain can cycle.
    """
    parts = []
    seen = set()
    current = exc
    prefix = ""
    while current is not None and len(parts) < limit:
        if id(current) in seen:
            parts.append("... (cycle)")
            break
        seen.add(id(current))
        parts.append(prefix + repr(current))
        if current.__cause__ is not None:
            current, prefix = current.__cause__, "caused by: "
        elif (current.__context__ is not None
                and not current.__suppress_context__):
            current, prefix = current.__context__, "raised while handling: "
        else:
            break
    return " | ".join(parts)


def get_store(root):
    """Return a shared ArchiveStore for a root path."""
    if root not in _STORES:
        _STORES[root] = ArchiveStore(root)
    return _STORES[root]


def archive_root(conf):
    """Resolve the archive root from a check's ``archive`` setting."""
    setting = conf.get("archive")
    if isinstance(setting, dict):
        return setting.get("root", DEFAULT_ROOT)
    return DEFAULT_ROOT


def user_agent(conf):
    """Resolve the User-Agent for an archived fetch.

    ``user_agent: false`` deliberately falls back to kibitzr's own header,
    for the case where a site treats an unknown agent worse than a known
    one. That is a choice worth making explicitly rather than by default.
    """
    setting = conf.get("user_agent", None)
    if setting is None:
        return DEFAULT_USER_AGENT
    if setting is False:
        return None
    return str(setting)


class CapturingSessionFetcher(SessionFetcher):
    """SessionFetcher that keeps the last response object, and fetches under
    limits an unattended collector can rely on.

    kibitzr's fetcher returns ``(ok, response.text)`` and drops the response,
    so status code, ETag and Last-Modified are lost before anything downstream
    can see them. A response hook captures them without touching the retry
    loop.

    The rest of this class overrides kibitzr's fetch path rather than extending
    it. Each override is here because the stock behaviour would let the archive
    record something untrue — see the individual methods. Derived from
    ``kibitzr/fetcher/simple.py`` (kibitzr, MIT).
    """

    def __init__(self, conf):
        super().__init__(conf)
        self.last_response = None
        # Taken off the session kibitzr built, before that session is replaced
        # below. This is what `user_agent: false` falls back to, so it has to
        # be kibitzr's own header and not requests' default.
        kibitzr_agent = self.session.headers.get("User-agent")
        # Replaces the session `SessionFetcher.__init__` built, which wraps
        # `requests.Session` in `CacheControl`. A cache is wrong here in a way
        # that is specific to archiving rather than to monitoring: the fetcher
        # is constructed once per check and lives for the whole process, so its
        # in-memory cache persists across polls, and a target serving a long
        # `max-age` would have its polls answered without the origin ever being
        # contacted. Those polls would be recorded as observations — and would
        # be indistinguishable from genuinely unchanged ones, which collapses
        # the exact distinction ("polled, unchanged" vs "not polled") this
        # archive exists to keep. The request-side no-cache headers cover
        # intermediaries the same way.
        self.session = requests.Session()
        self.session.headers.update({
            "User-agent": kibitzr_agent,
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        })
        # Filled in by `_vet` before each request; the adapter connects only to
        # what is in here, so validation and connection cannot disagree about
        # where the hostname points.
        self.pins = {}
        adapter = pinned_adapter(
            self.pins, allow_proxy=conf.get("allow_proxy", False),
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.max_response_bytes = int(
            conf.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES))
        self.max_fetch_seconds = float(
            conf.get("max_fetch_seconds", DEFAULT_MAX_FETCH_SECONDS))
        self.max_retry_seconds = float(
            conf.get("max_retry_seconds", DEFAULT_MAX_RETRY_SECONDS))
        self.encoding = conf.get("encoding")
        self.minimum_content_bytes = int(conf.get("minimum_content_bytes", 1))
        if not self.verify_cert:
            logger.warning(
                "TLS certificate verification is disabled for %s", self.url)
        agent = user_agent(conf)
        if agent is not None:
            # requests' header dict is case-insensitive, so this replaces
            # rather than duplicates the 'User-agent' set above.
            self.session.headers["User-Agent"] = agent
        self.session.hooks["response"].append(self._capture)

    def _capture(self, response, *args, **kwargs):  # pylint: disable=unused-argument
        self.last_response = response
        return response

    RETRIES = 3

    def fetch(self):
        """Retry transient errors, bounded in both count and wall-clock time.

        Upstream retries three times with no ceiling on the total; its own
        backoff for `Timeout` is ``60 * (retry + 1)``, so a target timing out
        three times holds the single-threaded scheduler for three minutes and
        every other check waits behind it.
        """
        retries = self.RETRIES
        retry_started = monotonic()
        for retry in range(retries):
            try:
                return self._fetch_once()
            except self.EXCEPTED as exc:
                elapsed = monotonic() - retry_started
                if retry < retries - 1 and elapsed < self.max_retry_seconds:
                    self.sleep_on_exception(
                        exc, retry, self.max_retry_seconds - elapsed)
                else:
                    raise
        # Unreachable while RETRIES is 3 — every iteration either returns or
        # raises. It is here so the (ok, content) contract cannot be broken by
        # a silent `None` if that count is ever changed.
        raise ConfigurationError(
            "Fetch retry count must be positive, got {!r}".format(retries))

    def _vet(self, url, original_url=None):
        """Validate a target and pin the connection to the addresses it was
        validated against."""
        url, addresses = vet_url(url, self.conf, original_url=original_url)
        self.pins[normalize_host(urlsplit(url).hostname)] = addresses
        return url

    def _timeout(self, deadline):
        """Per-hop (connect, read) timeouts clipped to the overall deadline.

        `timeout` is per-connect and per-read-idle, never a total: a chain of
        hops each answering just inside the read timeout, or trickling a byte
        at a time, could spend `(max_redirects + 1) * READ_TIMEOUT` without a
        single timeout firing. The deadline is the only thing that bounds the
        whole fetch, so every hop is clipped to what is left of it.
        """
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise requests.Timeout("Maximum fetch duration exceeded")
        return (min(CONNECT_TIMEOUT, remaining), min(READ_TIMEOUT, remaining))

    def _fetch_once(self):
        """One fetch, following redirects by hand so each hop can be vetted.

        requests follows redirects internally, which would place every hop
        after the first outside the check `vet_url` performs.
        """
        deadline = monotonic() + self.max_fetch_seconds
        # Re-vetted per attempt: a pin is only as good as the lookup behind it,
        # and a retry may be a minute after the last one.
        self.pins.clear()
        current_url = self._vet(self.url)
        max_redirects = int(self.conf.get("max_redirects", 5))
        for redirect_count in range(max_redirects + 1):
            response = self.session.get(
                current_url,
                allow_redirects=False,
                stream=True,
                timeout=self._timeout(deadline),
                verify=self.verify_cert,
            )
            if response.is_redirect or response.is_permanent_redirect:
                response.close()
                if redirect_count == max_redirects:
                    raise requests.TooManyRedirects(
                        "Exceeded {} redirects".format(max_redirects))
                location = response.headers.get("location")
                if not location:
                    raise requests.TooManyRedirects(
                        "Redirect response did not include Location")
                current_url = self._vet(
                    urljoin(current_url, location), original_url=self.url)
                continue
            return self._consume_response(response, deadline)
        raise requests.TooManyRedirects()

    def _consume_response(self, response, deadline):
        """Read the body under a size ceiling and the fetch deadline."""
        content = bytearray()
        try:
            for chunk in response.iter_content(CHUNK_SIZE):
                if monotonic() > deadline:
                    raise requests.Timeout("Maximum fetch duration exceeded")
                if not chunk:
                    continue
                if len(content) + len(chunk) > self.max_response_bytes:
                    raise requests.RequestException(
                        "Response exceeds max_response_bytes ({})".format(
                            self.max_response_bytes))
                content.extend(chunk)
        finally:
            response.close()
        ok = response.status_code in self.valid_http
        if ok and len(content) < self.minimum_content_bytes:
            # Empty string, not a message: the content slot is what gets
            # hashed and retained, so a diagnostic here would be archived as
            # though the page had changed to say it.
            logger.warning(
                "Response from %s is shorter than minimum_content_bytes (%d)",
                self.url, self.minimum_content_bytes)
            return False, ""
        return ok, self._decode(bytes(content), response)

    def _decode(self, content, response):
        """Decode the body, degrading rather than aborting on a bad charset.

        The operator's `encoding` is authoritative: if it is wrong, that is a
        config error and should be loud. Anything taken off the response is
        not — it is chosen by the page being monitored, and a hard error there
        hands the page a way to make itself un-archivable: serve bytes that do
        not decode, or name a codec that does not exist, and the check fails
        every time with a non-retriable error. So a response-supplied charset
        falls back instead, and a mangled page yields degraded content that
        still diffs and still gets recorded.
        """
        if self.encoding:
            try:
                return content.decode(self.encoding)
            except (LookupError, UnicodeDecodeError) as exc:
                raise ConfigurationError(
                    "Could not decode response from {} using configured "
                    "encoding {!r}".format(self.url, self.encoding)) from exc
        match = RE_CHARSET.search(response.headers.get("content-type", ""))
        encoding = match.group(1) if match else "utf-8"
        try:
            codecs.lookup(encoding)
        except LookupError:
            logger.warning(
                "Response from %s declared unknown charset %r; using utf-8",
                self.url, encoding)
            encoding = "utf-8"
        try:
            return content.decode(encoding)
        except LookupError:
            # `codecs.lookup` succeeding does not mean `bytes.decode` will:
            # bytes-to-bytes codecs (rot_13, zlib_codec) are in the registry,
            # and `decode` refuses them with LookupError rather than
            # UnicodeDecodeError.
            logger.warning(
                "Response from %s declared non-text charset %r; using utf-8",
                self.url, encoding)
            encoding = "utf-8"
        except UnicodeDecodeError:
            logger.warning(
                "Response from %s did not decode as %r; replacing bad bytes",
                self.url, encoding)
        return content.decode(encoding, errors="replace")

    def sleep_on_exception(self, exc, retry, max_sleep=None):
        """Back off between retries. Overrides a method that cannot run.

        Upstream (``kibitzr/fetcher/simple.py``) tests the backoff value with
        ``isinstance(seconds, collections.Callable)``. The ``collections``
        aliases for the ABCs were deprecated in Python 3.3 and **removed in
        3.10**, so on any currently supported interpreter this raises
        ``AttributeError`` — and it raises it inside the ``except`` block that
        handles a retriable error. The consequences compound:

        1. The retry loop never retries. The first transient blip is fatal.
        2. The AttributeError replaces the original exception, so the recorded
           cause describes our dependency rather than the target. That is how
           an hour of DNS unavailability on this laptop got written into the
           poll log as a Python attribute error.

        Fixed here rather than by patching the installed kibitzr: the venv is
        rebuildable and a patch there would be silently lost on reinstall,
        taking the archive's failure attribution with it. Reported upstream
        separately — this override is a local floor, not a substitute.

        ``max_sleep`` is what remains of the caller's retry budget. Without it
        the backoff is unbounded from the scheduler's point of view: kibitzr's
        own value for `Timeout` is ``60 * (retry + 1)``, and kibitzr runs
        checks on one thread, so a slow target would stall every other check.
        """
        for klass, seconds in self.RETRIABLE_EXCEPTIONS:
            if isinstance(exc, klass):
                if isinstance(seconds, collections.abc.Callable):
                    seconds = seconds(retry)
                limit = self.max_retry_seconds
                if max_sleep is not None:
                    limit = min(limit, max(0, max_sleep))
                seconds = min(seconds, limit)
                logger.warning(
                    "Retriable fetch error, sleeping %ss before retry %s: %r",
                    seconds, retry + 1, exc)
                sleep(seconds)
                break


class ArchivePromoter(URLPromoter):
    """Fetch, record the poll, retain the response, pass content through."""

    PRIORITY = 20  # above the built-in Requests (5) and Firefox (15) promoters

    def __init__(self, conf):
        super().__init__(conf)
        self.store = get_store(archive_root(conf))
        self._session_fetcher = None
        self._firefox = self.needs_firefox(conf)
        # Fingerprint of the regime this check is fetched under, recorded on
        # every poll so a later reader can tell a change in the collector from
        # a change in the target. Resolved once; it cannot move mid-process.
        self._fetch_id = check_fetch_id(conf)

    @classmethod
    def is_applicable(cls, conf):
        """Applicable to any URL check that opts in with ``archive``."""
        return all((
            URLPromoter.is_applicable(conf),
            bool(conf.get("archive")),
        ))

    def log_announcement(self):
        logger.info("Fetching and archiving %s at %s",
                    self.conf["name"], self.conf["url"])

    def fetch(self):
        """Record the poll, and report a failure rather than raising one.

        A raised fetch error does not stop this check, it stops the collector.
        Nothing between here and the top of the process catches anything:
        `Checker.check` calls `fetch` unguarded, and `App.execute_all` loops
        over the checkers unguarded, so one exception ends the run and every
        *other* check silently stops being polled. That is the worst available
        outcome for an archive whose losses are unrecoverable — time spent not
        collecting cannot be gone back for.

        Upstream already treats an unreachable target as a failed check rather
        than a fatal one whenever the failure arrives as an HTTP status: a 500
        returns `(False, body)` and the run continues. Only the exception path
        — a refused connection, a DNS failure, a certificate that will not
        verify — takes the process down with it. That asymmetry is not a
        decision anyone made, and this conforms the two: the poll is recorded
        as failed, the error text goes back as the report, and the scheduler
        moves on to the next check.

        Demonstrated live on 3 and 4 Aug 2026, when TLS to one target failed
        for a few minutes after boot and stopped collection on all six.
        """
        super().fetch()
        try:
            ok, content = self._do_fetch()
        except Exception as exc:  # noqa: BLE001 - recorded, then reported
            error = causal_chain(exc)
            self._record(ok=False, content=None, error=error)
            # Loud, because the poll log is the only thing that will remember
            # this and nothing is configured to alert on it.
            logger.error("Fetch failed for %s, continuing to the next check: %s",
                         self.conf["name"], error)
            return False, error
        self._record(ok=ok, content=content)
        return ok, content

    def _do_fetch(self):
        if self._firefox:
            # pylint: disable=import-outside-toplevel
            from kibitzr.fetcher.browser.fetcher import firefox_fetcher
            return firefox_fetcher(self.conf)
        if self._session_fetcher is None:
            self._session_fetcher = CapturingSessionFetcher(self.conf)
        return self._session_fetcher.fetch()

    def _response_metadata(self):
        """Return (status, etag, last_modified) if a response was captured."""
        fetcher = self._session_fetcher
        response = getattr(fetcher, "last_response", None) if fetcher else None
        if response is None:
            return None, None, None
        headers = response.headers
        return (response.status_code,
                headers.get("ETag"),
                headers.get("Last-Modified"))

    def _record(self, ok, content, error=None):
        status, etag, last_modified = self._response_metadata()
        try:
            record = self.store.record_poll(
                check_name=self.conf["name"],
                url=self.conf.get("url"),
                ok=ok,
                content=content,
                http_status=status,
                etag=etag,
                last_modified=last_modified,
                error=error,
                fetch_id_=self._fetch_id,
            )
        except Exception:  # noqa: BLE001
            # Archiving must never take down the check it is observing.
            logger.exception("Failed to record poll for %r", self.conf["name"])
            return
        if record.changed:
            logger.info("Archived change for %s (sha256 %s)",
                        self.conf["name"], record.content_sha256[:12])
        else:
            logger.debug("Archived unchanged poll for %s", self.conf["name"])
