"""Typed canonical Persona discovery client for the Agora interaction worker.

The worker's previous Persona discovery port imported a symbol that does not
exist anywhere in this repository and silently substituted an always-empty
stub on any failure. This module replaces that with a narrow, typed wrapper
over the same canonical `ReadSurfaceStore` the BFF process itself uses
(`read_store.ReadSurfaceStore`), so a worker and the BFF observe the same
Persona read surface.

Construction failures are not caught here. A caller that cannot build this
client has a missing required dependency and must fail startup/health rather
than fall back to an empty implementation.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from read_store import ReadSurfaceStore


@runtime_checkable
class PersonaReadPort(Protocol):
    """Narrow Persona discovery port the interaction worker depends on."""

    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        ...

    def get_capability_snapshot(self, snapshot_id: Optional[str]) -> Optional[Dict[str, Any]]:
        ...


def _bool_from_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_canonical_persona_client() -> PersonaReadPort:
    """Construct the canonical Persona read client.

    Raises whatever the underlying `ReadSurfaceStore` construction raises;
    callers must propagate that failure instead of substituting an empty
    Persona discovery implementation.
    """
    data_dir = os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")
    os.makedirs(data_dir, exist_ok=True)
    allow_local_snapshot_fallback = _bool_from_env(
        "PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK",
        default=False,
    )
    return ReadSurfaceStore(
        os.path.join(data_dir, "read_surfaces.json"),
        allow_local_snapshot_fallback=allow_local_snapshot_fallback,
    )
