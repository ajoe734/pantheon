"""Application dependencies and composition root for the Operator BFF."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from services.control_plane.bff.deployment.adapters import (
    DefaultDeploymentCommands,
    DeploymentReadSurfaceAdapter,
)
from services.control_plane.bff.deployment.ports import (
    DeploymentCommands,
    DeploymentQueries,
)
from services.control_plane.bff.ports import (
    ReadSurfacePorts,
    create_persona_registry_write_owner,
    create_ranking_write_owner,
    create_read_surface_ports,
)


@dataclass(frozen=True)
class AppDependencies:
    """Explicit composition root container for the Operator BFF.

    Provides typed domain query, command, and event dependencies for router
    factories, eliminating global service locator lookups.
    """

    deployment_queries: DeploymentQueries
    deployment_commands: DeploymentCommands
    read_surface: ReadSurfacePorts
    command_store: Any
    persona_write_owner: Any
    ranking_write_owner: Any
    settings_store: Any

    @classmethod
    def create_default(
        cls,
        *,
        deployment_queries: Optional[DeploymentQueries] = None,
        deployment_commands: Optional[DeploymentCommands] = None,
        read_surface: Optional[ReadSurfacePorts] = None,
        command_store: Optional[Any] = None,
        persona_write_owner: Optional[Any] = None,
        ranking_write_owner: Optional[Any] = None,
        settings_store: Optional[Any] = None,
    ) -> AppDependencies:
        """Construct the concrete production dependencies once during startup.

        Enforces that required command owners fail startup closed if absent
        or unconfigured.
        """
        resolved_ranking_write_owner = ranking_write_owner
        if resolved_ranking_write_owner is None:
            if create_ranking_write_owner is not None:
                resolved_ranking_write_owner = create_ranking_write_owner()
            if resolved_ranking_write_owner is None:
                raise RuntimeError("Required ranking write owner is absent; failing startup closed.")

        resolved_persona_write_owner = persona_write_owner
        if resolved_persona_write_owner is None:
            if create_persona_registry_write_owner is not None:
                resolved_persona_write_owner = create_persona_registry_write_owner()
            if resolved_persona_write_owner is None:
                raise RuntimeError("Required persona write owner is absent; failing startup closed.")

        resolved_read_surface = read_surface or create_read_surface_ports(
            persona_registry_store=resolved_persona_write_owner,
        )

        resolved_deployment_queries = deployment_queries or DeploymentReadSurfaceAdapter(
            read_surface=resolved_read_surface,
        )
        if not isinstance(resolved_deployment_queries, DeploymentQueries):
            raise TypeError(
                f"deployment_queries must implement DeploymentQueries protocol, got {type(resolved_deployment_queries)}"
            )

        resolved_deployment_commands = deployment_commands or DefaultDeploymentCommands()
        if not isinstance(resolved_deployment_commands, DeploymentCommands):
            raise TypeError(
                f"deployment_commands must implement DeploymentCommands protocol, got {type(resolved_deployment_commands)}"
            )

        bff_data_dir = os.environ.get("BFF_DATA_DIR", "/tmp/pantheon/bff")
        os.makedirs(bff_data_dir, exist_ok=True)
        if command_store is not None:
            resolved_command_store = command_store
        else:
            try:
                from services.control_plane.bff.command_queue import CommandStore
                resolved_command_store = CommandStore(os.path.join(bff_data_dir, "commands.jsonl"))
            except (ImportError, Exception):
                resolved_command_store = None

        if settings_store is not None:
            resolved_settings_store = settings_store
        else:
            try:
                from services.control_plane.bff.settings_store import SettingsStore
                resolved_settings_store = SettingsStore(os.path.join(bff_data_dir, "settings.json"))
            except (ImportError, Exception):
                resolved_settings_store = None

        return cls(
            deployment_queries=resolved_deployment_queries,
            deployment_commands=resolved_deployment_commands,
            read_surface=resolved_read_surface,
            command_store=resolved_command_store,
            persona_write_owner=resolved_persona_write_owner,
            ranking_write_owner=resolved_ranking_write_owner,
            settings_store=resolved_settings_store,
        )


__all__ = ["AppDependencies"]
