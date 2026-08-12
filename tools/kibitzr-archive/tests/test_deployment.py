"""Deployment checks must fail closed rather than print reassuring labels."""

import importlib.util
import os
import re
import subprocess
from datetime import timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_live_integrity_service_allows_only_unanchored_exposure():
    unit = (ROOT / "deploy/evidence-integrity.service").read_text()
    assert "archive fsck --strict --allow-unanchored" in unit


def test_backup_source_and_staged_copy_allow_unanchored_exposure():
    script = (ROOT / "deploy/backup-archive.sh").read_text()
    gates = re.findall(r'^"\$KIBITZR" archive fsck ([^\n]+)', script, re.MULTILINE)
    assert gates == [
        '--strict --allow-unanchored --root "$SOURCE" || \\',
        '--strict --allow-unanchored --root "$STAGING" || \\',
    ]


def test_preflight_remains_a_fully_strict_quiescent_gate():
    script = (ROOT / "deploy/preflight.sh").read_text()
    gates = re.findall(r'archive fsck ([^\n]+)', script)
    assert gates == [
        '--strict --root "$ARCHIVE" >/dev/null && ok FSCK || bad FSCK '
        '"archive fsck found damage or suspect state"'
    ]


def test_remote_backup_requires_explicit_stage_directory():
    env = os.environ.copy()
    env.pop("BACKUP_STAGE_DIR", None)
    result = subprocess.run(
        [str(ROOT / "deploy/backup-archive.sh"), "remote:bucket", "/missing"],
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "requires explicit BACKUP_STAGE_DIR" in result.stderr


@pytest.fixture
def health_module():
    path = ROOT / "deploy/health-check.py"
    spec = importlib.util.spec_from_file_location("deployment_health", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_health_parser_requires_machine_readable_time(health_module):
    with pytest.raises(ValueError, match="generation time"):
        health_module.body_time(b"<html>no timestamp</html>")


def test_health_parser_reads_utc_time(health_module):
    value = health_module.body_time(
        b'<span id="generated"><time datetime="2026-08-09T12:00:00Z">tick</time></span>'
    )
    assert value.tzinfo == timezone.utc


def test_health_parser_ignores_unrelated_datetime(health_module):
    value = health_module.body_time(
        b'<time datetime="2000-01-01T00:00:00Z">other</time>'
        b'<span id="generated"><time datetime="2026-08-09T12:00:00Z">tick</time></span>'
    )
    assert value.year == 2026
