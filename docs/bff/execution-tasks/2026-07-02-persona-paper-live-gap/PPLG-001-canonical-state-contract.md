# PPLG-001 - Canonical Persona Paper/Live State And Contract Alignment

Priority: P0

Area: Architecture, schemas, BFF contract

## Goal

Lock the paper-first persona lifecycle contract so all implementation tasks use
the same states, endpoints, DTOs, and old-spec supersession rules.

## Required Work

- Add or update canonical schemas for:
  - `PaperPersonaLaunch`
  - `PersonaReadinessProjection`
  - `PaperEvaluationSnapshot`
  - `PromotionScoreSnapshot`
  - `CohortRankingSnapshot`
  - `HumanReviewRequest`
  - `QuarterlyRebalanceProposal`
  - `RiskGuardrailEvent`
- Update BFF contract/OpenAPI docs for the endpoint families listed in the gap spec.
- Mark the old onboarding wizard interpretation as superseded for the primary create path.
- Keep the underlying Persona, Capital Pool, Approval, DeploymentPlan, and RuntimeBinding records atomic and auditable.

## Acceptance Criteria

- Contract docs state that user-facing persona creation completes to paper runtime or `setup_failed`.
- Contracts state canary/live/quarterly changes require human decision records.
- Contracts state automatic guardrails cannot promote or increase allocation.
- Schema tests or contract tests validate required fields and enum values.
- No old doc remains ambiguous about normal identity-only persona creation.

## Artifacts

- `docs/04/pantheon_persona_paper_live_gap_2026-07-02/GAP_AND_EXECUTION_PLAN.md`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `services/control-plane/bff/openapi*`
- `services/control-plane/bff/tests/*`
- `docs/contracts/*` or existing schema location selected by owner
