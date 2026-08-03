"""Pin outgoing connections to the addresses that were vetted for them.

``security.vet_url`` resolves a hostname to decide whether the target is
public. requests then resolves that same hostname again when it opens the
socket, and nothing carries the first answer over to the second. A hostile or
merely misconfigured nameserver can exploit the gap: answer the validating
lookup with a public address, answer the connecting lookup with ``127.0.0.1``
or a metadata endpoint. The name that was checked and the address that is
reached are simply two different facts.

The adapter here removes the second lookup. Connections are made to an address
the caller already vetted, and to nothing else — a host with no vetted
addresses is refused rather than resolved. ``Host`` header and TLS SNI still
carry the hostname, so certificate validation and virtual hosting are
unaffected: only the destination of the socket is pinned.

A proxy defeats all of this and cannot be made to work: the socket goes to the
proxy, and the *proxy* resolves the target name, on its own, later. That is the
validate/connect split again with an extra host in the middle. requests picks
proxies up from ``HTTP_PROXY``/``HTTPS_PROXY`` in the environment without
anything in the config mentioning them, and routes them through a separate
``ProxyManager`` that never sees the connection classes below — so this would
degrade silently rather than fail. A proxied request is therefore refused
unless the check sets ``allow_proxy``, which is an assertion that the proxy
itself is trusted to pick the destination.
"""
import logging

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import HTTPError

from kibitzr.exceptions import ConfigurationError


logger = logging.getLogger(__name__)


def normalize_host(hostname):
    """Key hostnames the way both urllib3 and DNS treat them: case- and
    trailing-dot-insensitive, without IPv6 literal brackets."""
    return hostname.strip("[]").rstrip(".").lower()


class _PinnedConnectionMixin:
    """Connect to ``pins[host]`` instead of resolving ``host`` again.

    urllib3 keeps the resolution target in ``_dns_host`` and derives ``host`` —
    used for the ``Host`` header and SNI — from it, so the address is swapped
    in only for the duration of the socket call and put back after.
    """

    # Replaced with the fetcher's registry by `pinned_adapter`.
    pins = {}

    def _new_conn(self):
        addresses = self.pins.get(normalize_host(self.host))
        if not addresses:
            # Fail closed. Reaching a host that nothing vetted is precisely the
            # case this adapter exists to prevent, so it must not quietly fall
            # back to an ordinary lookup.
            raise ConfigurationError(
                "Refusing connection to unvetted host {!r}".format(self.host)
            )
        original = self._dns_host
        last_error = None
        try:
            for address in addresses:
                self._dns_host = address
                try:
                    return super()._new_conn()
                except (OSError, HTTPError) as exc:
                    # Every address here came out of one vetted lookup, so
                    # trying the next is the same fallback `create_connection`
                    # would have done.
                    last_error = exc
        finally:
            self._dns_host = original
        raise last_error


class _PinnedHTTPAdapter(HTTPAdapter):

    def __init__(self, pool_classes, allow_proxy=False, **kwargs):
        self._pool_classes = pool_classes
        self._allow_proxy = allow_proxy
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        super().init_poolmanager(connections, maxsize, block=block, **kwargs)
        # Rebinding the instance attribute; the module-level mapping urllib3
        # ships is left alone.
        self.poolmanager.pool_classes_by_scheme = self._pool_classes

    def proxy_manager_for(self, proxy, **kwargs):
        # requests calls this only when a proxy actually applies to the
        # request, which makes it the exact point to refuse. Patching the
        # returned manager's pool classes would not help: what needs pinning is
        # the target, and under a proxy the target is resolved at the far end
        # of the connection, where nothing here reaches.
        if not self._allow_proxy:
            raise ConfigurationError(
                "Refusing to fetch through proxy {!r}: the proxy resolves the "
                "target itself, so the address that was vetted is not the one "
                "connected to. Set allow_proxy to accept that.".format(proxy)
            )
        logger.warning(
            "Fetching through proxy %s: address pinning does not apply, and "
            "the proxy decides what the hostname resolves to.", proxy,
        )
        return super().proxy_manager_for(proxy, **kwargs)


def pinned_adapter(pins, allow_proxy=False):
    """Return a requests adapter that only connects to addresses in ``pins``.

    ``pins`` maps a normalized hostname to the list of addresses vetted for it.
    It is held by reference, so a caller can re-pin between requests — which is
    what a redirect chain needs, each hop being vetted in turn.

    The connection classes are built per adapter rather than configured through
    the pool manager: urllib3 turns pool keyword arguments into a namedtuple
    pool key with a fixed set of fields, so an extra keyword would raise, and a
    dict value would not be hashable.
    """
    connection_classes = {
        scheme: type(
            "Pinned" + base.__name__,
            (_PinnedConnectionMixin, base),
            {"pins": pins},
        )
        for scheme, base in (
            ("http", HTTPConnection),
            ("https", HTTPSConnection),
        )
    }
    pool_classes = {
        scheme: type(
            "Pinned" + base.__name__,
            (base,),
            {"ConnectionCls": connection_classes[scheme]},
        )
        for scheme, base in (
            ("http", HTTPConnectionPool),
            ("https", HTTPSConnectionPool),
        )
    }
    return _PinnedHTTPAdapter(pool_classes, allow_proxy=allow_proxy)
