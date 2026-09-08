"""Agora research sub-module — capability: agora.research.v1."""
from .receipt import ResearchExecutionReceipt, resolve_run_provenance
from .dispatcher import (
    AuthenticStageAdapter,
    ResearchDispatcher,
    AdapterRegistry,
    build_authentic_adapter_registry,
)

__all__ = [
    "ResearchExecutionReceipt",
    "resolve_run_provenance",
    "AuthenticStageAdapter",
    "ResearchDispatcher",
    "AdapterRegistry",
    "build_authentic_adapter_registry",
]
