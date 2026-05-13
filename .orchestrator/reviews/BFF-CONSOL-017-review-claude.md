# Review: BFF-CONSOL-017 — Detail journey smoke B (post-fix)

Reviewer: Claude
Date: 2026-05-13
Status: APPROVED

## Scope

Post-fix review after Codex raised two findings:
1. Research detail linkage: original proof depended on `/api/v1/research/tickets/{id}` and `/api/v1/research/analysis/{id}` returning 200 — those intentionally use `include_local_fallback=false` and return 404.
2. Evolution program: `/bff/evolution-programs/evoprog-pack-b-001` returned typed 404 against original route wiring.

## Artifacts Reviewed

- `execute-plans/tests/e2e/detail-smoke-b.spec.ts`
- `support/evidence/BFF-CONSOL-017-detail-smoke-b.json`
- `services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py`
- `services/control-plane/bff/main.py` (relevant route handlers)

## Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| 5 family detail routes 2xx or typed 404 | PASS |
| v5 intervention detail 含 governed remediation skeleton | PASS |
| agora detail 顯示 topic 內歷史 event | PASS |
| research detail 連 analysis | PASS |
| artifacts detail 含 lineage | PASS |
| evidence JSON 含每個 family transcript | PASS |

## Verification Commands Run

```
PANTHEON_BFF_AUTH_STUB=true PANTHEON_BFF_AUTH_MODE=permissive python3 -m pytest services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py -v
# Result: 2 passed in 2.86s

PANTHEON_BFF_AUTH_STUB=true PANTHEON_BFF_AUTH_MODE=permissive python3 -m pytest \
  services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py \
  services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py \
  services/control-plane/bff/test_rw03_analyze_contract.py \
  services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py \
  services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q
# Result: 51 passed in 21.93s
```

## Findings

Codex's two review issues are fully resolved:

1. **Research linkage**: The spec now probes `/bff/research-experiments/{id}` (BFF route) which enriches the response with `analysis_ids` and `analysis_links` via `_bff_experiment_with_analysis_links()`. `/bff/research-analyses/{id}` also resolves 200. The strict `/api/v1` routes with `include_local_fallback=false` are correctly documented as out-of-scope for this BFF acceptance proof.

2. **Evolution program**: `/bff/evolution-programs/evoprog-pack-b-001` resolves 200 via `read_store.get_evolution_program()` through the local snapshot fallback. The Pack B fixture is present and both assertion tests confirm this.

3. **Playwright spec**: All acceptance assertions are correctly enforced — analysis linkage check (lines 144-162), remediation skeleton fields (lines 199-208), agora historical events (lines 248-250), artifact lineage_id (line 278), degraded-path typed 404 (lines 318-328).

4. **No regressions**: 51-test broader Pack B and read-surface suite passes clean.

No blockers. Approved and returned to Codex2 for closeout.
