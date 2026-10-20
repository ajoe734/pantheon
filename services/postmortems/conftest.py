"""Postmortems test environment.

``services.incident.reference_validation`` builds a ``RuntimeManagerClient`` at
import time, and that client refuses to start without
``PANTHEON_RUNTIME_MANAGER_URL``.  The production compose service supplies the
same value (docker-compose.yml, ``postmortems`` service environment), so the
tests only mirror what the deployed container already has.
"""
from __future__ import annotations

import os

os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://runtime-manager:8081")
