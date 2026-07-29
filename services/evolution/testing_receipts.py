"""Test-only receipt adapters for the Evolution dispatch boundary.

The production registry built by
:func:`services.evolution.dispatch_receipts.build_adapter_registry` only ever
contains adapters that read a real downstream service.  Tests cannot reach a
research orchestrator or a runtime manager, so they need a receipt source they
control.

Nothing here is reachable from production wiring: an adapter in this module only
takes effect if a test explicitly installs it into a registry.  Keeping it in a
separate, obviously-named module — rather than adding a "static receipt" mode to
the real adapters — means there is no configuration flag or environment variable
that could turn a production dispatch into a fabricated receipt.
"""
from __future__ import annotations

from typing import Any, Mapping

from services.evolution.dispatch_receipts import (
    OUTCOME_PENDING,
    OUTCOME_SUCCEEDED,
    DispatchReceipt,
)

RECEIPT_KIND = "test_downstream_record"


class ScriptedReceiptAdapter:
    """A downstream stand-in whose statuses the test sets explicitly.

    ``submit`` behaves like a real adapter: it returns a pending receipt with a
    deterministic reference derived from the decision id, so resubmitting the
    same intent re-attaches instead of creating a second reference.
    ``read_receipt`` returns whatever the test recorded for that reference,
    defaulting to still-pending.
    """

    kind = RECEIPT_KIND

    def __init__(self, plane: str = "research", *, always_succeeds: bool = False) -> None:
        self.plane = plane
        # ``always_succeeds`` is for suites whose subject is some *other*
        # invariant (routing, cooldown windows, review chains) and that only
        # need the receipt gate satisfied.  Suites that test the gate itself
        # script individual references instead.
        self.always_succeeds = always_succeeds
        self.statuses: dict[str, DispatchReceipt] = {}
        self.submissions: list[dict[str, Any]] = []
        self.readbacks: list[str] = []

    @staticmethod
    def reference_for(decision_id: str) -> str:
        return f"test-run-{decision_id}"

    def set_succeeded(self, decision_id: str, *, status: str = "completed") -> str:
        reference = self.reference_for(decision_id)
        self.statuses[reference] = DispatchReceipt(
            outcome=OUTCOME_SUCCEEDED,
            downstream_kind=self.kind,
            downstream_ref_id=reference,
            downstream_status=status,
            detail=f"scripted terminal success for {reference}",
        )
        return reference

    def set_receipt(self, decision_id: str, receipt: DispatchReceipt) -> str:
        reference = self.reference_for(decision_id)
        self.statuses[reference] = receipt
        return reference

    def submit(self, intent: Mapping[str, Any]) -> DispatchReceipt:
        self.submissions.append(dict(intent))
        reference = self.reference_for(str(intent.get("decision_id") or ""))
        return DispatchReceipt(
            outcome=OUTCOME_PENDING,
            downstream_kind=self.kind,
            downstream_ref_id=reference,
            detail=f"scripted submission for {reference}",
        )

    def read_receipt(
        self,
        downstream_ref_id: str,
        *,
        expected_intent: Mapping[str, Any] | None = None,
    ) -> DispatchReceipt:
        self.readbacks.append(downstream_ref_id)
        scripted = self.statuses.get(downstream_ref_id)
        if scripted is not None:
            return scripted
        if self.always_succeeds:
            return DispatchReceipt(
                outcome=OUTCOME_SUCCEEDED,
                downstream_kind=self.kind,
                downstream_ref_id=downstream_ref_id,
                downstream_status="completed",
                detail=f"scripted always-succeeds downstream {downstream_ref_id}",
            )
        return DispatchReceipt(
            outcome=OUTCOME_PENDING,
            downstream_kind=self.kind,
            downstream_ref_id=downstream_ref_id,
            detail="scripted downstream has not reported a terminal state",
        )


ALL_PLANES = ("research", "governance", "runtime", "deployment")


def install_scripted_adapter(
    registry: dict[str, Any],
    *planes: str,
    always_succeeds: bool = False,
) -> ScriptedReceiptAdapter:
    """Install one scripted adapter across the given planes and return it."""
    targets = planes or ("research",)
    adapter = ScriptedReceiptAdapter(plane=targets[0], always_succeeds=always_succeeds)
    for plane in targets:
        registry[plane] = adapter
    return adapter


def receipt_body(decision_id: str) -> dict[str, str]:
    """The ``execution_receipt`` payload matching :class:`ScriptedReceiptAdapter`."""
    return {
        "downstream_kind": RECEIPT_KIND,
        "downstream_ref_id": ScriptedReceiptAdapter.reference_for(decision_id),
    }
