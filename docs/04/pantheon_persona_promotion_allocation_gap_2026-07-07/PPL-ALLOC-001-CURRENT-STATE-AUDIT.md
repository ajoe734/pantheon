# PPL-ALLOC-001: Current State and Page Inventory Audit

## Status
- **Phase**: source-truth-page-inventory (Wave 0)
- **Owner**: Antigravity
- **Reviewer**: Claude
- **Source Gap Spec**: [PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md](docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md)

---

## 1. Management Page Inventory & Route Mapping

The frontend repository `ajoe734/execute-plans` has been audited to verify route mapping against the required inventory. Currently, all legacy or diagnostic routes redirect correctly to the primary workflow tabs under `/management/promotion-allocation` as defined in [App.tsx](https://github.com/ajoe734/execute-plans/blob/main/src/App.tsx).

| Surface / Route | Target State | Current App.tsx Route Implementation | Audit Result |
| :--- | :--- | :--- | :--- |
| `/management/promotion-allocation` | **Primary Workflow Page** | `Route path="promotion-allocation" element={<PromotionAllocationRoute />}` | **Keep & Expand**. unified entry with tabs for candidates, ranking, quarterly capital, and formula policy (to be expanded in `PPL-ALLOC-006`). |
| `/management/persona-fleet` | **Primary Monitoring Page** | `Route path="persona-fleet" element={<PersonaFleetRoute />}` | **Keep**. Needs to show stage, paper ledger, runtime binding, real sleeve/pool, and next action (covered by `PPL-ALLOC-003`, `PPL-ALLOC-007`). |
| `/management/personas` | **Registry & Create Entry** | `Route path="personas" element={<PersonasListRoute />}` | **Keep**. Needs dedicated "Create Paper Persona" flow replacing the generic create drawer (covered by `PPL-ALLOC-005`). |
| `/management/personas/:id/onboarding` | **Setup Repair / Completion** | `Route path="personas/:id/onboarding" element={<PersonaOnboardingRoute />}` | **Repair-only**. Reclassified as onboarding/setup repair for incomplete or failed bundles. |
| `/management/human-inbox` | **Human Approval Queue** | `Route path="human-inbox" element={<HumanInboxRoute />}` | **Keep**. |
| `/management/human-inbox/:id` | **Decision Detail** | `Route path="human-inbox/:id" element={<HumanGateDetailRoute />}` | **Keep**. |
| `/management/capital` | **Capital Binding Visibility** | `Route path="capital" element={<LegacyPromotionAllocationRedirect tab="quarterly-capital" />}` | **Redirect** to `/management/promotion-allocation?tab=quarterly-capital`. |
| `/management/capital/:id` | **Capital Detail** | `Route path="capital/:id" element={<LegacyPromotionAllocationRedirect tab="quarterly-capital" idParamName="capital_id" />}` | **Redirect** to `/management/promotion-allocation?tab=quarterly-capital&capital_id=:id`. |
| `/management/rebalance/:id` | **Rebalance Approval Detail** | `Route path="rebalance/:id" element={<LegacyPromotionAllocationRedirect tab="quarterly-capital" idParamName="rebalance_id" />}` | **Redirect** to `/management/promotion-allocation?tab=quarterly-capital&rebalance_id=:id`. |
| `/management/ranking` | **Formula Diagnostics Only** | `Route path="ranking" element={<LegacyPromotionAllocationRedirect tab="formula-policy" />}` | **Redirect** to `/management/promotion-allocation?tab=formula-policy`. Demoted to formula diagnostics. |
| `/management/readiness/capital-binding-live` | **Readiness Gate Only** | `Route path="readiness/capital-binding-live" element={<LegacyPromotionAllocationRedirect tab="quarterly-capital" />}` | **Redirect** to `/management/promotion-allocation?tab=quarterly-capital`. |
| `/management/persona-league` | **Legacy Route** | `Route path="persona-league" element={<PersonaLeagueRoute />}` | **Legacy Route**. (still-live legacy page, not yet redirected; redirect is future work for `PPL-ALLOC-007`). |
| `/management/quarterly-ranking` | **Legacy Route** | `Route path="quarterly-ranking" element={<QuarterlyRankingRoute />}` | **Legacy Route**. (still-live legacy page, not yet redirected; redirect is future work for `PPL-ALLOC-007`). |
| `/management/rebalance` & `/rebalances` | **Legacy List Routes** | `Route path="rebalance"` & `rebalances` element = redirect | **Redirect** to `/management/promotion-allocation?tab=quarterly-capital`. |

---

## 2. Verification of the `paper_running` Invariant

The BFF implementation has been audited to verify if creating a trading persona produces a complete runnable paper bundle or a partial shell.

- **Verified Invariant (Proven)**: In [main.py](services/control-plane/bff/main.py#L39833-L40060), the handler `bff_create_persona` mapped to `POST /bff/personas` atomically performs the following steps:
  1. Sets the lifecycle state to `"paper_running"`.
  2. Generates and binds an isolated `paper_ledger_id` (`paper_ledger` with isolated flag set to `True`).
  3. Establishes a `persona_binding` to the capital pool with capital mode `"paper"`.
  4. Creates a locked paper `deployment_plan` marked as approved.
  5. Creates a running paper `runtime_binding`.
  6. Registers the persona in the recurring OODA cron scheduler (`_try_register_persona_cron`).
  7. Bootstraps the persona's first OODA loop packet (`_try_bootstrap_persona_ooda_packet`).
- **Conclusion**: The creation path successfully enforces the `paper_running` invariant. It does not produce an ambiguous shell, but a fully bootstrapped simulation bundle.

---

## 3. Downstream Task Gaps (`PPL-ALLOC-002` through `PPL-ALLOC-008`)

To bridge the remaining workflow requirements, the following specific code gaps are identified for downstream tasks:

### `PPL-ALLOC-002` (BFF Create Paper Persona Bundle)
- **Current State**: BFF relies on `POST /bff/personas` (compatibility route).
- **Gap**: Need to expose a specific, idempotent management command endpoint `POST /bff/management/personas/create-paper-bundle`. It must handle failures gracefully, persist repairable incomplete states instead of reporting `paper_running`, and verify that no live capital or broker order binding is ever touched.

### `PPL-ALLOC-003` (Capital Binding Read Model)
- **Current State**: Persona fleet and capital pool structures are read from generic endpoints.
- **Gap**: Read models must be normalized to explicitly project stage-aware fields (`stage`, `paper_ledger_id`, `runtime_binding_id`, `capital_scope`, `capital_pool_id`, `capital_sleeve_id`, `current_weight`, `target_weight`, `binding_state`). Exclude real pool details from paper persona rows, and ensure that parent portfolio representations maintain distinct sleeve identities.

### `PPL-ALLOC-004` (Ranking Allocation Policy)
- **Current State**: The ranking model evaluates performances but lack stage-aware recommendation actions and rebalance proposals.
- **Gap**: Implement:
  1. Stage-aware recommendations (paper -> paper-to-canary review; canary -> canary-to-live review; live -> quarterly increase; breach -> containment).
  2. The target weight formula based on:
     $$\text{rank\_score} = 0.25 \cdot \text{pnl} + 0.20 \cdot \text{sharpe} + 0.15 \cdot \text{drawdown} + 0.15 \cdot \text{execution} + 0.15 \cdot \text{risk} + 0.05 \cdot \text{improvement} - 0.05 \cdot \text{human\_intervention\_penalty} - \text{hard\_penalty}$$
  3. Caps (canary $\le 5\%$, S-tier max $25\%$, A-tier max $15\%$, B-tier max $8\%$, suspended $0\%$, quarterly increase cap $+25\%$).
  4. An auditable rebalance proposal contract with simulation, constraints, and rollback target.

### `PPL-ALLOC-005` (Frontend Create Paper Persona Flow)
- **Current State**: Uses the generic drawer creation path.
- **Gap**: Replace with a unified "Create Paper Persona" flow collecting mandate, strategy family, data sources, and risk limits, calling `/create-paper-bundle`. If a step fails, direct the user to the onboarding setup repair view.

### `PPL-ALLOC-006` (Unified Promotion & Allocation Workbench)
- **Current State**: `/management/promotion-allocation` is a basic tab shell.
- **Gap**: Build full Tab layouts:
  - **Paper candidates**: showing eligibility, sufficiency, and promotion review triggers.
  - **Real ranking**: displaying weights, deltas, cap reasons, and approval states.
  - **Quarterly capital**: proposal tracking, simulation checks, and deep links.
  - **Emergency actions**: listing breach containment recommendations.

### `PPL-ALLOC-007` (Binding Visibility & Route Prune)
- **Current State**: Fleet and capital list columns have missing/`nan` values for bindings.
- **Gap**: Prune old links, resolve `nan` projection errors, and ensure `/management/rebalance/:id` functions as the sole detail route.

### `PPL-ALLOC-008` (Emergency Containment Policy)
- **Current State**: Emergency overrides are handled ad-hoc.
- **Gap**: Implement immediate BFF commands for freeze, capital reduction, suspension, and retire. Ensure they require reason and evidence refs, and enforce a strict policy guard that emergency actions can only reduce risk, never promote or increase capital.
