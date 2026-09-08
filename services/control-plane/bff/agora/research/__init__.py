"""Agora research sub-module — capability: agora.research.v1."""
from .receipt import ResearchExecutionReceipt, resolve_run_provenance
from .dispatcher import (
    AuthenticResearchBackendClient,
    AuthenticStageAdapter,
    ResearchDispatcher,
    AdapterRegistry,
    build_authentic_adapter_registry,
    build_canonical_research_backend_clients,
)

__all__ = [
    "ResearchExecutionReceipt",
    "resolve_run_provenance",
    "AuthenticResearchBackendClient",
    "AuthenticStageAdapter",
    "ResearchDispatcher",
    "AdapterRegistry",
    "build_authentic_adapter_registry",
    "build_canonical_research_backend_clients",
]
