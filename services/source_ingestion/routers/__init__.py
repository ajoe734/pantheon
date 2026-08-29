"""Routers package for Source Ingestion."""

from .catalog_controller import create_catalog_controller_router
from .ingest_operations import create_ingest_operations_router
from .management import create_management_router
from .observability import create_observability_router
from .proposals import create_proposals_router

__all__ = [
    "create_catalog_controller_router",
    "create_ingest_operations_router",
    "create_management_router",
    "create_observability_router",
    "create_proposals_router",
]
