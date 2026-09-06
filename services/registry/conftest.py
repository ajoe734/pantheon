"""Test-only backend selection default for the Registry service test suite.

architecture-resumption-sa-sd.md §3.1 requires ``REGISTRY_STORE_BACKEND`` to
fail closed in a staging/production persistence posture rather than silently
default to the in-memory store (see ``storage.build_registry_store``). This
package's unit tests exercise the mounted FastAPI app via
``fastapi.testclient.TestClient`` against the in-memory backend, so this
conftest opts the whole test-run into that documented test-only default up
front — an explicit choice a human can find and audit, not an accidental
unset-config fallback.

Tests that need the real Postgres backend (services/registry/test_owner_durability.py)
override this by setting ``REGISTRY_STORE_BACKEND=postgres`` themselves inside
their own fixture and restore the prior value (this default) afterward.
"""
from __future__ import annotations

import os

os.environ.setdefault("REGISTRY_STORE_BACKEND", "memory")
