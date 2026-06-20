# Review: AG-XR-OPENAPI-001-SIDECAR-ACCEPTANCE-FOLLOWUP-2

- Reviewed by: `Claude2`
- Review date: `2026-06-20`
- Task status: `review → review_approved`
- Owner: `Claude`

## Verdict: APPROVED

The sidecar acceptance packet is complete, accurate, and meets all five stated
completion criteria. No canonical truth was modified. The packet is suitable for
handoff to the parent task owner.

## Evidence Reviewed

| Artifact | Checked |
|---|---|
| `support/sidecars/AG-XR-OPENAPI-001/AG-XR-OPENAPI-001-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` | Full read |
| `ai-status.json` (canonical root) | Task exists at `review`; owner `Claude`; reviewer `Claude2` |
| Branch | `task/AG-XR-OPENAPI-001-SIDECAR-ACCEPTANCE-FOLLOWUP-2` — correct |
| `git status --short` | Only untracked task-brief; no dirty canonical files |

## Checklist Assessment

The packet verified delivery against 14 acceptance items:

| Result | Count | Notes |
|---|---|---|
| PASS | 13 | All route completeness, concurrency headers, schema refs, security, bundle integrity checks pass |
| DEVIATION | 1 | Dashboard routes included; scope deviation from original acceptance checklist |

**13 of 14 checks pass.** The 1 deviation is correctly documented without making a
scope ruling — appropriate for a sidecar `acceptance_packet`.

## Specific Findings

### Route Inventory
- 8 servant routes: all present, concurrency headers correct ✓
- 13 workshop routes: all present including 3 previously seed-gap routes ✓
- 3 OpenClaw adapter internal routes: all present, `x-internal: true`, capability restrictions enforced ✓
- 11 dashboard routes: present as scope deviation (see below)

### OperationId Variants
Routes 4–8 (servant session sub-routes) and routes 10–11 (workshop research-run and
consultation) carry minor operationId variants from the original suggestions. These
are non-breaking; path and verb contracts are correct. The note to downstream tasks
(`AG-BE-ID-002`, `AG-BE-SW-001`) to use paths not operationIds is correct guidance.

### Dashboard Scope Deviation — Accepted as Recorded
The packet accurately records that 11 `agora.dashboard.v2` routes were included in
`agora_v1_1.openapi.yaml` against the original acceptance requirement.

The deviation section is well-structured: it lists all 11 routes, verifies their
concurrency model, and presents two interpretations (intentional bundling vs
extraction needed) without adjudicating. This is the correct posture for a sidecar.

**Action required from parent owner only:** Confirm whether the dashboard bundling
was intentional and update AG-XR-DASH-001 scope accordingly.

### Bundle Hash Gap
The finding that `bundle_index.v1_1.json` does not yet include a hash for
`agora_v1_1.openapi.yaml` is correct and is accurately tagged as an AG-XR-003
prerequisite, not a blocker on AG-BE-ID-002 or AG-BE-SW-001.

### Dependency Map
Updated Mermaid dependency map is accurate:
- AG-XR-OPENAPI-001 marked done ✓
- AG-BE-ID-002 and AG-BE-SW-001 marked unblocked ✓
- AG-XR-003 dependency on bundle hash addition ✓
- AG-XR-DASH-001 dashed arrow for ambiguous scope ✓

## Follow-On Items for Parent Owner

1. **Dashboard scope ruling**: Confirm whether the `agora.dashboard.v2` bundling in
   `agora_v1_1.openapi.yaml` was intentional, and whether AG-XR-DASH-001 now only
   needs backend implementation rather than a new YAML.

2. **Bundle hash**: Add `openapi/agora_v1_1.openapi.yaml` sha256 to
   `bundle_index.v1_1.json` before AG-XR-003 can finalize its deploy validator.

These are parent-owner decisions; they do not block the approval of this sidecar packet.

## Review Notes (ZH)

審查通過 — sidecar acceptance packet 完整且正確，無 canonical truth 異動||後續追蹤：(1) parent owner 需確認 dashboard 路由是否為有意擴展，並更新 AG-XR-DASH-001 範疇；(2) bundle_index.v1_1.json 缺少 agora_v1_1.openapi.yaml hash，AG-XR-003 finalize 前需補齊。
