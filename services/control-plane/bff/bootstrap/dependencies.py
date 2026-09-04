"""Application dependencies and composition contracts for the Operator BFF."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from services.control_plane.bff.deployment.ports import DeploymentQueries
from services.control_plane.bff.ports import (
    ReadSurfacePorts,
    create_read_surface_ports,
)


@dataclass
class AppDependencies:
    """Explicit container for typed BFF domain dependencies.

    Eliminates global service locator lookups by providing explicit typed query,
    command, and event interfaces for each domain router factory.
    """

    queries: DeploymentQueries
    read_store: Optional[ReadSurfacePorts] = None

    @classmethod
    def create_default(
        cls,
        *,
        read_store: Optional[ReadSurfacePorts] = None,
        deployment_queries: Optional[DeploymentQueries] = None,
    ) -> AppDependencies:
        store = read_store or create_read_surface_ports()
        queries = deployment_queries or store
        return cls(
            queries=queries,
            read_store=store,
        )


__all__ = ["AppDependencies"]
