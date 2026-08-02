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
import collections.abc
import logging
from time import sleep

from kibitzr.fetcher.loader import URLPromoter
from kibitzr.fetcher.simple import SessionFetcher

from .store import (FETCH_SEMANTICS_VERSION, ArchiveStore, fetch_id)


logger = logging.getLogger(__name__)

DEFAULT_ROOT = "archive"

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
    """SessionFetcher that keeps the last response object.

    kibitzr's fetcher returns ``(ok, response.text)`` and drops the
    response, so status code, ETag and Last-Modified are lost before
    anything downstream can see them. A response hook captures them
    without touching the retry loop.
    """

    def __init__(self, conf):
        super().__init__(conf)
        self.last_response = None
        agent = user_agent(conf)
        if agent is not None:
            # SessionFetcher sets 'User-agent'; requests' header dict is
            # case-insensitive, so this replaces rather than duplicates it.
            self.session.headers["User-Agent"] = agent
        self.session.hooks["response"].append(self._capture)

    def _capture(self, response, *args, **kwargs):  # pylint: disable=unused-argument
        self.last_response = response
        return response

    def sleep_on_exception(self, exc, retry):
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
        """
        for klass, seconds in self.RETRIABLE_EXCEPTIONS:
            if isinstance(exc, klass):
                if isinstance(seconds, collections.abc.Callable):
                    seconds = seconds(retry)
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
        super().fetch()
        try:
            ok, content = self._do_fetch()
        except Exception as exc:  # noqa: BLE001 - logged then re-raised
            self._record(ok=False, content=None, error=causal_chain(exc))
            raise
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
