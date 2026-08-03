"""Network-boundary checks for archived fetches.

Where this came from, and why it lives here: this began as hardening applied
to a working copy of kibitzr's own ``fetcher/`` subsystem. Deploying that copy
was rejected deliberately. "We ran kibitzr 8.0.0 from PyPI plus this plugin at
commit X" is a collection stack a third party can reconstruct and audit; "we
ran a private fork" is not, and for an archive whose entire premise is removing
the need to trust the archivist, that is the wrong direction to drift. So the
part that matters for collection is carried here, in the public plugin, and the
fork stays undeployed.

The boundary this draws is narrow on purpose. It is not a general SSRF defence
for arbitrary user-supplied URLs; the targets are a short list written by the
operator. What it prevents is a *configured* target silently becoming a
different target — through a redirect, or through a lookup that answers
differently the second time.
"""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests
from kibitzr.exceptions import ConfigurationError


def resolve_target(hostname, conf):
    """Resolve ``hostname`` once and return every address it answers with.

    The addresses come back to the caller because validating a *name* is not a
    boundary on its own: whoever answers the second lookup — the one the HTTP
    client makes when it opens the socket — decides what is actually connected
    to. Callers are expected to pin the connection to what this returned; see
    ``pinning``.
    """
    # A name that will not resolve is a transient network condition, not a bad
    # config, and it must stay retriable: `ConfigurationError` is not in
    # `SessionFetcher.EXCEPTED`, so raising it here would skip the retry loop
    # entirely — and nothing between `Checker.check` and `Application.run`
    # catches it, so one flaky lookup would take down the whole daemon and
    # every other check with it. That is not hypothetical here: this archive
    # already has 25 polls written during an hour when DNS was unavailable at
    # boot. `requests.ConnectionError` is what a DNS failure raised before this
    # module existed, which is what the retry loop is written against.
    #
    # A *refused* target below is a different thing: that is policy, and it
    # stays a hard ConfigurationError so retrying cannot wear it down.
    try:
        addresses = sorted({
            item[4][0]
            for item in socket.getaddrinfo(hostname, None)
        })
    except socket.gaierror as exc:
        raise requests.ConnectionError(
            "Could not resolve fetch target {!r}".format(hostname)
        ) from exc
    if not addresses:
        raise requests.ConnectionError(
            "Fetch target {!r} resolved to no addresses".format(hostname)
        )
    if not conf.get("allow_private_network", False):
        private = [
            address
            for address in addresses
            if not ipaddress.ip_address(address).is_global
        ]
        if private:
            raise ConfigurationError(
                "Refusing private or non-public fetch target: {!r} -> {}"
                .format(hostname, ", ".join(private))
            )
    return addresses


def vet_url(url, conf, original_url=None):
    """Validate a fetch target; return its normalized URL and vetted addresses.

    Private-network targets and cross-origin redirects are denied by default.
    Operators opt in explicitly with ``allow_private_network`` and
    ``allow_cross_origin_redirects``.

    A cross-origin redirect matters to an archive more than it does to a
    monitor. The poll row records the *configured* URL, so a redirect that
    quietly lands somewhere else would file another site's content under this
    check's name — an untrue statement about what was observed where, which is
    the one thing the record must not contain.
    """
    if original_url:
        url = urljoin(original_url, url)
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigurationError(
            "Fetcher URL must use http or https: {!r}".format(url)
        )
    is_cross_origin = False
    if original_url:
        original = urlsplit(original_url)
        is_cross_origin = (
            parsed.scheme, parsed.hostname, parsed.port
        ) != (
            original.scheme, original.hostname, original.port
        )
    # Every target is resolved and checked, including the first, non-redirect
    # fetch — the resolution happens even under `allow_private_network`,
    # because the addresses are what the connection gets pinned to.
    addresses = resolve_target(parsed.hostname, conf)
    if is_cross_origin and not conf.get("allow_cross_origin_redirects", False):
        raise ConfigurationError(
            "Refusing cross-origin redirect to {!r}".format(url)
        )
    return url, addresses
