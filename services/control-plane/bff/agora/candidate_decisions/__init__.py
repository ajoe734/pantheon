"""Persona-derived candidate decisions and canonical review receipts.

Exports are lazy so importing the durable store does not initialize the
interaction router or its provider client dependencies.
"""

__all__ = [
    "AuthoritativeValidationRequest",
    "AuthoritativeValidationReceipt",
    "CandidateDecisionCommand",
    "CandidateDecisionConflict",
    "CandidateDecisionService",
    "CandidateDecisionStore",
    "CandidateFromMeasureCommand",
    "FormalApprovalReceipt",
]


def __getattr__(name: str):
    if name in {
        "AuthoritativeValidationRequest",
        "AuthoritativeValidationReceipt",
        "CandidateDecisionCommand",
        "CandidateFromMeasureCommand",
        "FormalApprovalReceipt",
    }:
        from . import models

        return getattr(models, name)
    if name in {"CandidateDecisionConflict", "CandidateDecisionStore"}:
        from . import store

        return getattr(store, name)
    if name == "CandidateDecisionService":
        from .service import CandidateDecisionService

        return CandidateDecisionService
    raise AttributeError(name)
