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
    if installed:
        logger.info("Recording normalised hashes for %d check(s)", installed)
