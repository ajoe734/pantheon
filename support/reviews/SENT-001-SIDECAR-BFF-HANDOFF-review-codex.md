# SENT-001-SIDECAR-BFF-HANDOFF Review - Codex

Task: SENT-001-SIDECAR-BFF-HANDOFF - BFF/frontend handoff packet
Owner: Claude2
Reviewer: Codex
Review date: 2026-05-16
Disposition: changes requested

## Finding

1. `meta.surfaces.sentinel_findings.source` is documented as the logical data tier, but the BFF emits read-store provenance values.
   - Packet references: `support/sidecars/SENT-001/SENT-001-SIDECAR-BFF-HANDOFF.md:101`, `support/sidecars/SENT-001/SENT-001-SIDECAR-BFF-HANDOFF.md:171`
   - The packet example shows `status: "ok"` with `source: "incidents"` and a `meta.degradation` block. That combination is not produced by `_sem_final_list_response`: degradation is added only when `_surface_degradation_reason(...)` returns a non-ok reason.
   - The packet also says `source` is `"incidents"` for the primary tier and `"sentinel_findings"` for fallback. In current BFF code, the route chooses a dataset name for `_dataset_surface_status(...)`, but the emitted `surface["source"]` comes from `read_store.dataset_source(dataset)` unless it is explicitly forced to `"missing"`.
   - Relevant code: `services/control-plane/bff/main.py:24415`, `services/control-plane/bff/main.py:25152`, `services/control-plane/bff/main.py:3947`, `services/control-plane/bff/read_store.py:7193`.

Impact: frontend implementers could branch on `source == "incidents"` or `source == "sentinel_findings"` and never match real payloads such as `local_snapshot`, service-store/client provenance values, or `missing`. The rendering rules should keep using `status` and `source == "missing"` for unavailable handling, but the packet must not advertise logical dataset names as emitted `source` values.

Required fix: update the response-shape example and Data Source Fallback Logic section to distinguish logical dataset selection from emitted provenance. Either remove the non-real `source: "incidents"` / `"sentinel_findings"` examples or replace them with accurate provenance language and a note that the chosen logical tier is not currently exposed as a distinct response field.

## Verification

```bash
git show --check --stat 5d1ca2c7
# sidecar commit adds only support/sidecars/SENT-001/SENT-001-SIDECAR-BFF-HANDOFF.md

pytest services/control-plane/bff/test_sent001_sentinel_findings_contract.py -q
# 16 passed in 18.20s
```

Additional review source checks:

- `services/control-plane/bff/main.py:24367-24417` confirms the dedicated filtered route and validation.
- `services/control-plane/bff/main.py:25141-25170` confirms list response metadata/degradation construction.
- `services/control-plane/bff/read_store.py:1517-1536` confirms incidents-first, sentinel_findings-fallback dataset selection.
- `services/control-plane/bff/read_store.py:7193-7244` confirms `dataset_source(...)` returns provenance values, not logical dataset names.
