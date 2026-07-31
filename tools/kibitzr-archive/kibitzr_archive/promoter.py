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
import logging

from kibitzr.fetcher.loader import URLPromoter
from kibitzr.fetcher.simple import SessionFetcher

from .store import ArchiveStore


logger = logging.getLogger(__name__)

DEFAULT_ROOT = "archive"

_STORES = {}


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
        self.session.hooks["response"].append(self._capture)

    def _capture(self, response, *args, **kwargs):  # pylint: disable=unused-argument
        self.last_response = response
        return response


class ArchivePromoter(URLPromoter):
    """Fetch, record the poll, retain the response, pass content through."""

    PRIORITY = 20  # above the built-in Requests (5) and Firefox (15) promoters

    def __init__(self, conf):
        super().__init__(conf)
        self.store = get_store(archive_root(conf))
        self._session_fetcher = None
        self._firefox = self.needs_firefox(conf)

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
            self._record(ok=False, content=None, error=repr(exc))
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
