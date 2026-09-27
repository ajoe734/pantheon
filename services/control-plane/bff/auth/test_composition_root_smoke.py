"""Composition-root smoke test proving default main.py wiring for auth.

Extracted this generation (BFF-TEST-FULL-MIGRATION-CORRECTIVE-001, P1 AC1/AC2)
out of auth/test_policy.py so that file itself no longer imports the BFF
composition root: this is the one test in the auth suite whose entire purpose
is to prove that main.py's *default* composition (not a hand-built app in a
test fixture) wires auth_deps, session_lifecycle_store, and guards correctly.
That requires importing the real services.control_plane.bff.main module, so
it belongs in the composition_allowlist alongside the other whole-app
composition tests (smoke_test.py, test_bff_main_composition.py, etc.) rather
than being tracked as an unreviewed offender.
"""
from __future__ import annotations

import subprocess
import sys


def test_composition_root_smoke():
    """Smoke test running in a subprocess proving default composition in main.py
    wires auth_deps, session_lifecycle_store, and guards correctly.
    """
    code = (
        "import tempfile, os\n"
        "with tempfile.TemporaryDirectory(prefix='bff-smoke-') as tmpdir:\n"
        "    os.environ['BFF_DATA_DIR'] = tmpdir\n"
        "    os.environ['PANTHEON_BFF_AUTH_STUB'] = 'true'\n"
        "    os.environ['PANTHEON_BFF_AUTH_MODE'] = 'permissive'\n"
        "    from services.control_plane.bff import main as bff_main\n"
        "    from fastapi.testclient import TestClient\n"
        "    client = TestClient(bff_main.app)\n"
        "    headers = {'Authorization': 'Bearer smoke-op:operator'}\n"
        "    r1 = client.get('/bff/me', headers=headers)\n"
        "    assert r1.status_code == 200, f'Expected 200, got {r1.status_code}'\n"
        "    r2 = client.post('/bff/logout', headers=headers)\n"
        "    assert r2.status_code == 200, f'Expected 200, got {r2.status_code}'\n"
        "    r3 = client.get('/bff/me', headers=headers)\n"
        "    assert r3.status_code == 401, f'Expected 401, got {r3.status_code}'\n"
        "    # Check guard canonical binding\n"
        "    assert getattr(bff_main.auth_deps.raise_if_session_logged_out, '_canonical_guard', False) is True\n"
        "    print('COMPOSITION_SMOKE_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "COMPOSITION_SMOKE_OK" in result.stdout
