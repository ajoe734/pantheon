"""Research execution receipt contract and server-side provenance resolution for Agora.

Implements SD §6.2:
  ResearchExecutionReceipt:
    receipt_id: string
    run_id: string
    executor: string
    mode: real|simulation
    backend_reference: string|null
    artifact_digest: string|null
    correlation_id: string
    completed_at: datetime

Agora resolves the receipt by run_id and verifies owner, correlation, and
terminal state. Public request payloads cannot set has_real_receipt or an
equivalent trust bit. Unknown receipt version returns unavailable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, Tuple

VALID_MODES = frozenset({"real", "simulation"})
VALID_PROVENANCE_VALUES = frozenset({"real", "simulation", "fixture", "unavailable"})


@dataclass
class ResearchExecutionReceipt:
    """Authentic receipt emitted by the research execution owner."""

    receipt_id: str
    run_id: str
    executor: str
    mode: Literal["real", "simulation"]
    correlation_id: str
    completed_at: str
    backend_reference: Optional[str] = None
    artifact_digest: Optional[str] = None
    spec_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchExecutionReceipt":
        return cls(
            receipt_id=str(data["receipt_id"]),
            run_id=str(data["run_id"]),
            executor=str(data["executor"]),
            mode="real" if data.get("mode") == "real" else "simulation",
            correlation_id=str(data.get("correlation_id") or ""),
            completed_at=str(data.get("completed_at") or datetime.now(timezone.utc).isoformat()),
            backend_reference=data.get("backend_reference"),
            artifact_digest=data.get("artifact_digest"),
            spec_version=str(data.get("spec_version", "1.0")),
        )


def resolve_run_provenance(
    store: Any,
    run: Dict[str, Any],
    *,
    expected_correlation_id: Optional[str] = None,
    expected_owner: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Resolve and verify authentic server-side execution receipt for a research run.

    Returns (provenance_str, receipt_dict_or_None).

    Invariants:
      - Non-terminal run status returns "unavailable".
      - Unknown receipt version returns "unavailable".
      - Receipt mode must be 'real' or 'simulation'.
      - If receipt is missing:
          - If run requested real mode without authentic execution receipt, returns "simulation".
          - Otherwise returns current run.provenance or "unavailable".
      - If expected_correlation_id is provided, mismatch returns "unavailable".
      - If expected_owner is provided, mismatch returns "unavailable".
    """
    run_id = run.get("run_id")
    if not run_id:
        return "unavailable", None

    status = str(run.get("execution_status") or "").lower()
    terminal_statuses = {"succeeded", "completed", "failed", "cancelled", "timed_out"}
    if status not in terminal_statuses:
        return "unavailable", None

    # Resolve receipt from store
    receipt_dict: Optional[Dict[str, Any]] = None
    if hasattr(store, "get_execution_receipt"):
        receipt_dict = store.get_execution_receipt(run_id)

    if receipt_dict is None:
        # No authentic server-side receipt found
        # If the run requested real backend, unreceipted output is downgraded to simulation
        requested_mode = (run.get("backend") or {}).get("mode") or run.get("requested_mode")
        if requested_mode == "real":
            return "simulation", None
        return run.get("provenance") or "simulation", None

    # Validate receipt structure and version
    spec_version = str(receipt_dict.get("spec_version", "1.0"))
    if spec_version != "1.0":
        return "unavailable", None

    if receipt_dict.get("run_id") != run_id:
        return "unavailable", None

    receipt_mode = str(receipt_dict.get("mode") or "").lower()
    if receipt_mode not in VALID_MODES:
        return "unavailable", None

    if expected_correlation_id is not None:
        receipt_corr = str(receipt_dict.get("correlation_id") or "")
        if receipt_corr != expected_correlation_id:
            return "unavailable", None

    if expected_owner is not None:
        receipt_executor = str(receipt_dict.get("executor") or "")
        if receipt_executor != expected_owner:
            return "unavailable", None

    return receipt_mode, receipt_dict
