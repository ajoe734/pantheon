# SVC-GOVERNANCE-API-SIDECAR-ACCEPTANCE Review — Claude

- Date: 2026-04-28
- Task: `SVC-GOVERNANCE-API-SIDECAR-ACCEPTANCE` — sidecar acceptance packet and dependency map for parent `SVC-GOVERNANCE-API`
- Parent: `SVC-GOVERNANCE-API` (parent owner: Claude, parent reviewer: Codex)
- Sidecar Owner: Codex
- Sidecar Reviewer: Claude
- Helper Kind: `acceptance_packet`
- Verdict: **APPROVED — return to owner for finalization**

## Artifact reviewed

- `support/sidecars/SVC-GOVERNANCE-API/SVC-GOVERNANCE-API-SIDECAR-ACCEPTANCE.md` (new, untracked).

`git status --short` confirms the sidecar created only this support file. No
canonical L1 docs, family contract, per-service contracts, runtime/registry
code, or compose wiring were edited by this sidecar. Other modifications in the
worktree (e.g. `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `docker-compose.yml`,
`services/runtime-manager/main.py`, etc.) were produced by earlier in-progress
tasks (SVC-RUNTIME-CONTROL, SVC-SERVICE-DISPOSITION) and are not attributable
to this sidecar.

## Acceptance criteria check

1. **Create support artifacts only** — pass. Sole new file is the support
   packet under `support/sidecars/SVC-GOVERNANCE-API/`.
2. **Do not edit canonical truth** — pass. Spot-checked against current
   git status and the packet's own claim of no canonical edits.
3. **Hand off to assigned reviewer** — pass. Handoff queued to Claude in
   `ai-status.json` at 2026-04-28T13:35:39Z.

## Fact-check on packet claims

All load-bearing facts in the packet were cross-verified:

- **Family contract exists** — `services/control-plane/governance/service_family_contract.md`
  is present and matches the four-member table the packet uses
  (governance/8082, deployment/8095, capital/8092, evolution/8093) plus the
  delegated runtime-manager/8081 boundary.
- **Per-service contracts exist** — `services/governance/contract.md`,
  `services/deployment/contract.md`, `services/capital/contract.md`, and
  `services/control-plane/governance/evolution_decision.contract.md` all exist.
- **Compose env vars** — `docker-compose.yml` lines 269–276 publish
  `PANTHEON_INTERNAL_API_URL`, `PANTHEON_RUNTIME_MANAGER_URL`,
  `PANTHEON_GOVERNANCE_APPROVAL_API_URL`, `PANTHEON_DEPLOYMENT_API_URL`,
  `PANTHEON_CAPITAL_API_URL`, `PANTHEON_EVOLUTION_API_URL` to the BFF, and the
  legacy compatibility alias `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093`
  is correctly described as a backward-compat path scheduled for retirement
  through SVC-SURFACES.
- **Compose ports & healthchecks** — runtime-manager `18081:8081` (`/__health__`),
  governance `18082:8082` (`/health`), capital `18092:8092` (`/health`),
  evolution `18093:8093` (`/health`), deployment `18095:8095` (`/health`).
- **Service boundary** — packet's separation of `RuntimeBinding` /
  kill-switch / safe-mode / operator command ownership to runtime-manager,
  and `ApprovalDecision` / `DeploymentPlan(Saga)` / `CapitalPool` /
  `PersonaCapitalBinding` / `EvolutionDecision` ownership to the four
  governance-family services, matches `service_family_contract.md` §3–§4.
- **Dependency map** — `SVC-BASELINE` parent dependency is `done` in
  `ai-status.json`. Downstream consumers `SVC-SURFACES`, `SVC-COMPOSE`, and
  `SVC-SERVICE-DISPOSITION` are correctly identified; their dependency edges
  on this family / boundary are visible in the corresponding `depends_on`
  fields.
- **Test evidence** — re-ran the focused test command listed in §6:
  `python3 -m pytest services/governance/test_governance_api.py
  services/capital/test_service.py services/deployment/test_service.py
  services/evolution/test_evolution_service.py
  services/runtime-manager/test_internal_api_routes.py` →
  `105 passed in 5.92s`. Matches packet's claim of 105 passed in 6.02s.

## Cross-document consistency

- The packet does not contradict the family contract on write authority,
  read discovery, or the runtime-control disjointness rule.
- The packet correctly classifies `RuntimeBinding` as an explicit *delegated*
  service surface owned by `runtime-manager`, rather than as a governance-family
  domain object — consistent with §3 of `service_family_contract.md` and the
  Hard Rule in §4.
- The reviewer checklist in §7 accurately reflects the parent's three
  acceptance items, and the recommended parent-owner use in §8 is properly
  scoped: it does not assert SVC-SURFACES completion, only that SVC-SURFACES
  has concrete service targets to consume.

## Verdict

Approved. Returning to Codex for finalization to `done`. The packet is a
faithful, testable acceptance/dependency snapshot for `SVC-GOVERNANCE-API` and
respects the support-only constraint. The parent-task owner (Claude) retains
full authority over whether and how this packet is folded into the eventual
`SVC-GOVERNANCE-API` formal review.
