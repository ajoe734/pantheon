import subprocess
import sys
from pathlib import Path


def test_telemetry_package_defers_schema_dependency_but_keeps_ingest_export():
    root = Path(__file__).resolve().parents[3]
    code = """
import sys
import services.telemetry
assert 'services.telemetry.ingest_svc' not in sys.modules
from services.telemetry import TelemetryIngestService
assert TelemetryIngestService.__module__ == 'services.telemetry.ingest_svc'
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
