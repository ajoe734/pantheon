"""Typed canonical Persona discovery client for the Agora interaction worker.

The worker's Persona discovery port provides a narrow, typed wrapper
over the canonical read surface ports the BFF process itself uses,
so a worker and the BFF observe the same Persona read surface.

Construction failures are not caught here. A caller that cannot build this
client has a missing required dependency and must fail startup/health rather
than fall back to an empty implementation.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

try:
    from ports import ReadSurfacePorts, create_read_surface_ports
except ImportError:
    from services.control_plane.bff.ports import (  # type: ignore[no-redef]
        ReadSurfacePorts,
        create_read_surface_ports,
    )


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

    Constructs a canonical read surface port client; callers must propagate
    any failure instead of substituting an empty Persona discovery implementation.
    """
    return create_read_surface_ports()

