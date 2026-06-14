# V7 — OpenAPI structural quality (direction G/D, broadening)

- Date: 2026-06-14
- Branch: task/verify-v7-openapi-quality
- Non-duplication: no brief covers OpenAPI quality/lint (bff-route-diff.yml diffs routes
  between deploys; this checks the spec's internal structural integrity). Distinct.

## Verification & result (`scripts/audit_openapi_quality.py`, vs live BFF spec)
Live spec: **447 paths / 497 operations / 34 schemas**. Checked:
- duplicate operationIds: **0**
- operations missing operationId: **0**
- operations missing a `responses` block: **0**
- orphan component schemas (defined but never `$ref`'d): **0**

Result: **OK — no structural OpenAPI defects.** The contract surface is structurally
clean; nothing to fix this round.

## Deliverable
Reusable structural OpenAPI gate (reads a file or fetches a URL; exit 1 on any defect),
so contract regressions (dup opids, response-less ops, orphan schemas) are caught going
forward. Complements bff-route-diff.yml (deploy-to-deploy route diff) with intra-spec
integrity.
