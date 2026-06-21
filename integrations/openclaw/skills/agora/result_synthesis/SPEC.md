# Skill — agora-result-synthesis

> Pantheon-side implementation of the C1 design-closure skill.
> Canonical SPEC source: `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/result-synthesis/SPEC.md`
> Implementation: `integrations/openclaw/skills/agora/result_synthesis/skill.py`
> Task: AG-BE-RS-004
> Depends on: AG-BE-RS-002 (ResearchRunSummary layer), AG-XR-OPENAPI-004 (v1.3 bundle)

---

## Purpose

把 ResearchRun、ConsultMemo、Evidence、Backtest／OOS 與風險結果整合成交易員可討論的評斷、版本 patch 與下一步，不暴露工程內部複雜度。每個結論必須 grounded 在 evidence_refs；不得無根據生成。

---

## Input

```ts
type ResultSynthesisInput = {
  strategySpecRef: string;         // → strategy_spec_ref
  baseVersionId: string;           // → base_version_id
  researchRunRefs: string[];       // → research_run_refs  (at least 1)
  consultMemoRefs: string[];       // → consult_memo_refs  (may be empty)
  evidenceRefs: string[];          // → evidence_refs      (at least 1)
  userDecisionStyleRef?: string;   // → user_decision_style_ref
};
```

---

## Output

```ts
type ResultSynthesisOutput = {
  verdict: "promising"|"needs_revision"|"insufficient"|"reject";
  confidence: number;              // 0..1
  coreMetrics: Record<string,number>;
  strengths: string[];
  weaknesses: string[];
  regimeFindings: string[];
  costCapacityFindings: string[];
  proposedVersionPatches: unknown[];
  unresolvedDecisions: unknown[];
  userFacingDiscussionCard: string;
  evidenceRefs: string[];          // NON-EMPTY unless verdict=="insufficient"

  // Common result envelope (C1 §3)
  status: "completed"|"needs_user"|"blocked"|"failed";
  blockingReasons: string[];
  warnings: string[];
};
```

---

## Rules

- 結論必須區分 in-sample、OOS、paper、shadow。
- 不得把 stub/smoke 結果說成 production proof。
- Patch 需 base version、原因、預期效果與重新驗證計畫。
- 若 evidence 相互矛盾，保留 conflict（放入 unresolved_decisions，不可壓制）。
- 不提供直接 live enable（output 僅為 proposal/candidate，不執行治理）。
- 不得自創 schema/欄位/route/enum。
- 不得擴張 capability allowlist。
- 不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

---

## Evidence Grounding Enforcement

Per task AG-BE-RS-004 core acceptance criterion:

> synthesis 輸出符合 C1 SPEC 且每結論有 evidence_refs；無 ungrounded 主張

Enforced by `run_result_synthesis()`:

1. `research_run_refs` must be non-empty (INPUT_SCHEMA_INVALID).
2. `evidence_refs` in input must be non-empty (INPUT_SCHEMA_INVALID).
3. If `verdict != "insufficient"`, output `evidence_refs` must be non-empty; otherwise blocked as INSUFFICIENT_EVIDENCE.
4. If synthesis adapter reports any research run with backend `mode == "stub"` or `"fixture"`, a STUB_RESULT_NOT_PRODUCTION_PROOF warning is emitted and verdict is downgraded to `needs_revision` or `insufficient`.

---

## Failure Codes

Per C1 SPEC §5:

| Code | Trigger |
|---|---|
| `INPUT_SCHEMA_INVALID` | `research_run_refs` or input `evidence_refs` is empty |
| `SYNTHESIS_ADAPTER_UNAVAILABLE` | `synthesis_adapter=None` (degraded mode) |
| `INSUFFICIENT_EVIDENCE` | Adapter returned non-insufficient verdict but empty `evidence_refs` |

Warnings (non-blocking):

| Warning | Trigger |
|---|---|
| `STUB_RESULT_NOT_PRODUCTION_PROOF` | Any research run reported with backend mode stub/fixture |

---

## Privacy Boundary

Per C1 SPEC §Common Hard Rules:

- Only research run refs, consult memo refs, and evidence refs are passed to the adapter — never raw prompts, user identities, or Journal content.
- Output is a proposal; it does not execute governance, capital binding, or broker orders.

---

## Golden Evals (C1 SPEC §6)

### Eval 1 — V3→V4 threshold/liquidity change

Input: research runs for base V3 and candidate V4 versions; evidence refs covering threshold and liquidity metrics.
Expected: verdict="promising", `core_metrics` includes quantitative before/after comparison (e.g., `sharpe_v3`, `sharpe_v4`, `liquidity_v3`, `liquidity_v4`); evidence_refs non-empty; proposed_version_patches list patch with rationale and revalidation_plan.

### Eval 2 — OOS failure, IS pass

Input: research runs where in-sample metrics pass but OOS metrics fail; evidence refs for both.
Expected: verdict="needs_revision" or "reject"; weaknesses describe OOS failure; evidence_refs non-empty. IS result alone must NOT yield "promising".

### Eval 3 — Consult disagreement (risk vs alpha persona)

Input: consult_memo_refs with opposing risk-persona and alpha-persona conclusions.
Expected: `unresolved_decisions` preserves both positions verbatim; `user_facing_discussion_card` describes both; servant does NOT suppress either perspective.

---

## Failure Behavior

- Failure result must not be disguised as a natural-language success answer.
- Degraded mode (no synthesis adapter): return blocked with SYNTHESIS_ADAPTER_UNAVAILABLE; no verdict forged.
- Stub/fixture run detected: downgrade verdict per §Evidence Grounding Enforcement; add warning.
- Evidence-less non-insufficient verdict: block with INSUFFICIENT_EVIDENCE; do not return fabricated grounding.
