"""Deployment checks must fail closed rather than print reassuring labels."""

import importlib.util
import os
import subprocess
from datetime import timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


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
        b'<time datetime="2026-08-09T12:00:00Z">tick</time>'
    )
    assert value.tzinfo == timezone.utc
