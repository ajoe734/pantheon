"""agora-strategy-completeness OpenClaw skill — Pantheon-side implementation.

Canonical skill name: agora-strategy-completeness
C1 spec location: docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/strategy-completeness/SPEC.md
A1 scoring spec:  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/A1_next_best_question_scoring_spec.md
Implementation path: integrations/openclaw/skills/agora/strategy_completeness/
Service layer: services/research/strategy_spec/completeness.py
Task: AG-BE-SW-003
"""
from .skill import (
    CompletenessInput,
    CompletenessOutput,
    run_strategy_completeness,
)

__all__ = [
    "CompletenessInput",
    "CompletenessOutput",
    "run_strategy_completeness",
]
