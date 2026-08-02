"""Capture post-transform content, via the kibitzr.before_start entry point.

The fetcher promoter sees the response as fetched and nothing else, because
kibitzr composes a check as fetch -> transform -> notify and the fetcher is the
first of those. So the raw hash can be recorded there, but the normalised hash
cannot: by the time the transform chain has run, the fetcher is long done.

``kibitzr.before_start`` receives the constructed ``Checker`` objects before the
scheduler starts, which is the one documented seam where the transform pipeline
is reachable. For each archived check we wrap a single transform in its pipeline
so it hashes its own input on the way past.

WHICH transform matters. A pipeline like::

    - css: '#contents'
    - text
    - changes: verbose

produces a *diff* as its final output, and an empty one whenever nothing moved.
Hashing the end of the pipeline would therefore hash a report about the document
rather than the document, and would record identical hashes for "unchanged" and
"changed back". The normalised document is the input to the first reporting
transform, so that is where the capture goes. With no reporting transform in the
chain, the end of the pipeline is the document and the capture goes there.
"""
import logging


logger = logging.getLogger(__name__)

# Transforms that consume a document and emit a report about it. Anything listed
# here ends the "this is still the document" part of the pipeline.
REPORTING_TRANSFORMS = ("changes",)


def rule_name(rule):
    """Return the transform name from a pipeline rule.

    Rules are either a bare string (``text``) or a single-item mapping
    (``{css: main}``), matching kibitzr's own parsing.
    """
    if isinstance(rule, dict):
        return next(iter(rule), None)
    return rule


def transform_rules(conf):
    """Return a check's transform rules as a list."""
    rules = conf.get("transform", [])
    if isinstance(rules, str):
        return [rules]
    return list(rules or [])


def capture_index(rules):
    """Return the pipeline index whose INPUT is the normalised document.

    That is the position of the first reporting transform, or the length of the
    pipeline when there is none — in which case the capture happens after the
    last transform rather than before any.
    """
    for index, rule in enumerate(rules):
        if rule_name(rule) in REPORTING_TRANSFORMS:
            return index
    return len(rules)


def install(checker, store):
    """Wrap one checker's pipeline so it records normalised content."""
    conf = checker.conf
    name = conf["name"]
    rules = transform_rules(conf)
    index = capture_index(rules)
    pipeline = checker.transform

    def record(content):
        if not content:
            return
        try:
            store.record_normalisation(name, content, transform_conf=rules)
        except Exception:  # noqa: BLE001
            # Same rule as the promoter: observing a check must never be able
            # to break it.
            logger.exception("Failed to record normalisation for %r", name)

    transforms = getattr(pipeline, "transforms", None)
    if transforms is None:
        logger.warning(
            "Check %r has no inspectable transform pipeline; "
            "normalised hashes will not be recorded for it.", name)
        return False

    if index < len(transforms):
        original = transforms[index]

        def capturing(content, _original=original):
            record(content)
            return _original(content)

        transforms[index] = capturing
    else:
        # No reporting transform: hash whatever the pipeline finally produces.
        original_call = pipeline.run_pipeline

        def capturing_pipeline(ok, content, _original=original_call):
            ok, result = _original(ok, content)
            if ok:
                record(result)
            return ok, result

        pipeline.run_pipeline = capturing_pipeline
        pipeline.__call__ = capturing_pipeline
        checker.transform = capturing_pipeline
    return True


#: Seconds per schedule unit, for reducing kibitzr's rules to one number.
_UNIT_SECONDS = {
    "seconds": 1, "second": 1,
    "minutes": 60, "minute": 60,
    "hours": 3600, "hour": 3600,
    "days": 86400, "day": 86400,
    "weeks": 604800, "week": 604800,
}


def declared_period(conf):
    """Return the intended polling period in seconds, or None if not derivable.

    kibitzr's config loader consumes ``period`` and replaces it with a list of
    ``TimelineRule(interval, unit, at)``, so by the time this hook runs the YAML
    key is gone and the rules are what the scheduler will actually honour —
    which is the thing worth recording as intent.

    Rules pinned to a wall-clock time (``at``) do not reduce to a period and are
    reported as None rather than guessed at. Recording a wrong intent would be
    worse than recording none: gaps would then be judged against a schedule
    nobody ever declared.
    """
    rules = conf.get("schedule") or []
    periods = []
    for rule in rules:
        interval = getattr(rule, "interval", None)
        unit = getattr(rule, "unit", None)
        at = getattr(rule, "at", None)
        if at is not None or interval is None:
            return None
        seconds = _UNIT_SECONDS.get(unit)
        if seconds is None:
            return None
        periods.append(interval * seconds)
    if not periods:
        return None
    # Several rules means several chances to poll, so the shortest is the
    # interval a gap should be judged against.
    return min(periods)


def declare_intent(checker, store):
    """Record the schedule and fetch regime this check is running under.

    Both are append-on-change: a restart that alters nothing writes nothing, so
    the annotation chain stays a log of actual regime changes rather than a
    log of restarts.
    """
    from .promoter import check_fetch_id  # noqa: PLC0415
    from .store import FETCH_SEMANTICS_VERSION  # noqa: PLC0415

    conf = checker.conf
    name = conf["name"]

    period = declared_period(conf)
    if period is None:
        logger.warning(
            "Check %r has no reducible polling period; gaps in its series "
            "cannot be read against declared intent.", name)
    elif store.declare_schedule(name, period):
        logger.info("Declared schedule for %r: every %ss", name, period)

    fingerprint = check_fetch_id(conf)
    if store.declare_fetch_regime(
        fingerprint,
        FETCH_SEMANTICS_VERSION,
        "retry loop restored over upstream's removed collections.Callable; "
        "transient fetch errors are now retried before being recorded as "
        "failures, so failure counts are not comparable across this point",
        check_name=name,
    ):
        logger.info("Recorded fetch-regime change for %r (%s)",
                    name, fingerprint[:12])


def before_start(app, checkers):  # noqa: ARG001 - signature fixed by kibitzr
    """Install normalisation capture on every check with ``archive`` set."""
    # Imported here so that merely loading the entry point does not pull in the
    # store and its sqlite connection at kibitzr import time.
    from .promoter import archive_root, get_store  # noqa: PLC0415

    installed = 0
    for checker in checkers:
        if not checker.conf.get("archive"):
            continue
        store = get_store(archive_root(checker.conf))
        if install(checker, store):
            installed += 1
        try:
            declare_intent(checker, store)
        except Exception:  # noqa: BLE001
            # Same rule as everywhere else here: recording context about a
            # check must never be able to stop the check from running.
            logger.exception("Failed to declare intent for %r",
                             checker.conf.get("name"))
    if installed:
        logger.info("Recording normalised hashes for %d check(s)", installed)
