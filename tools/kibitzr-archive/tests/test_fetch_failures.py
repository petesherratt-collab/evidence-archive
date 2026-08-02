"""Failure handling: retrying transient errors, and attributing them truthfully.

Both behaviours under test were absent, and their absence is what wrote 25 polls
into the live archive blaming a Python attribute error for an hour of DNS
unavailability.
"""
import socket

import pytest

pytest.importorskip("kibitzr")

from kibitzr_archive.promoter import (  # noqa: E402
    CapturingSessionFetcher, causal_chain, check_fetch_id,
)


# -- cause attribution -----------------------------------------------------

def test_chain_names_the_error_raised_while_handling_another():
    """The exact shape that produced the false records: a handler failing
    inside an except block, hiding the failure it was handling."""
    try:
        try:
            raise socket.gaierror(-3, "Temporary failure in name resolution")
        except OSError:
            raise AttributeError(
                "module 'collections' has no attribute 'Callable'")
    except AttributeError as exc:
        chain = causal_chain(exc)

    assert chain.startswith("AttributeError(")
    assert "raised while handling: " in chain
    assert "Temporary failure in name resolution" in chain


def test_chain_follows_explicit_causes():
    try:
        try:
            raise ValueError("underlying")
        except ValueError as exc:
            raise RuntimeError("surface") from exc
    except RuntimeError as exc:
        chain = causal_chain(exc)

    assert "caused by: ValueError('underlying')" in chain


def test_chain_respects_suppressed_context():
    """`raise ... from None` is a deliberate statement that the context is not
    the cause; recording it anyway would be inventing an attribution."""
    try:
        try:
            raise ValueError("irrelevant")
        except ValueError:
            raise RuntimeError("surface") from None
    except RuntimeError as exc:
        chain = causal_chain(exc)

    assert chain == "RuntimeError('surface')"


def test_chain_is_bounded_and_survives_a_cycle():
    first = ValueError("first")
    second = ValueError("second")
    first.__cause__ = second
    second.__cause__ = first

    chain = causal_chain(first)

    assert "cycle" in chain
    assert len(chain) < 500


def test_plain_exception_is_unchanged():
    assert causal_chain(ValueError("alone")) == "ValueError('alone')"


# -- the retry loop --------------------------------------------------------

def test_sleep_on_exception_survives_a_callable_backoff(monkeypatch):
    """Upstream raises AttributeError here on Python 3.10+, killing the retry
    and replacing the real error with its own."""
    fetcher = CapturingSessionFetcher({"name": "c", "url": "http://x"})
    slept = []
    monkeypatch.setattr("kibitzr_archive.promoter.sleep", slept.append)
    monkeypatch.setattr(fetcher, "RETRIABLE_EXCEPTIONS",
                        [(OSError, lambda retry: retry + 1)], raising=False)

    fetcher.sleep_on_exception(OSError("boom"), retry=1)

    assert slept == [2]


def test_sleep_on_exception_handles_a_constant_backoff(monkeypatch):
    fetcher = CapturingSessionFetcher({"name": "c", "url": "http://x"})
    slept = []
    monkeypatch.setattr("kibitzr_archive.promoter.sleep", slept.append)
    monkeypatch.setattr(fetcher, "RETRIABLE_EXCEPTIONS",
                        [(OSError, 5)], raising=False)

    fetcher.sleep_on_exception(OSError("boom"), retry=0)

    assert slept == [5]


def test_unretriable_exceptions_do_not_sleep(monkeypatch):
    fetcher = CapturingSessionFetcher({"name": "c", "url": "http://x"})
    slept = []
    monkeypatch.setattr("kibitzr_archive.promoter.sleep", slept.append)
    monkeypatch.setattr(fetcher, "RETRIABLE_EXCEPTIONS",
                        [(KeyError, 5)], raising=False)

    fetcher.sleep_on_exception(OSError("boom"), retry=0)

    assert slept == []


def test_upstream_backoff_config_is_actually_exercised(monkeypatch):
    """Guards against the override drifting away from what kibitzr ships:
    run it against the real RETRIABLE_EXCEPTIONS, not a stand-in."""
    fetcher = CapturingSessionFetcher({"name": "c", "url": "http://x"})
    slept = []
    monkeypatch.setattr("kibitzr_archive.promoter.sleep", slept.append)

    assert fetcher.RETRIABLE_EXCEPTIONS, "kibitzr defines retriable exceptions"
    klass, _ = fetcher.RETRIABLE_EXCEPTIONS[0]
    fetcher.sleep_on_exception(klass("boom"), retry=0)

    assert len(slept) == 1


# -- fingerprint agreement -------------------------------------------------

def test_hook_and_promoter_agree_on_the_fingerprint():
    """The annotation naming a regime and the rows claiming it must match."""
    conf = {"name": "c", "url": "http://x", "archive": True}

    assert check_fetch_id(conf) == check_fetch_id(dict(conf))
    # A browser-driven check is a different fetch regime.
    assert check_fetch_id(conf) != check_fetch_id(dict(conf, delay=5))
