"""
Pantheon Research Replication Gate

First-pass gate that validates research candidates before registry admission.
Sits between RS-001/RS-002 research ingestion and REG-001 registry entry.
"""

from gate import ReplicationGate, create_promotion_request
from gate_schema import (
    ReplicationRequest,
    ReplicationResponse,
    ReplicationStatus,
    CandidateAdmissionStatus,
    RegistryPromotionRequest,
)
from gate_config import GateConfig, AdmissionRules

__version__ = "1.0.0"
__all__ = [
    "ReplicationGate",
    "create_promotion_request",
    "ReplicationRequest",
    "ReplicationResponse",
    "ReplicationStatus",
    "CandidateAdmissionStatus",
    "RegistryPromotionRequest",
    "GateConfig",
    "AdmissionRules",
]
