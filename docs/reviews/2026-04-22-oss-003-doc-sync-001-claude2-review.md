# Review: OSS-003-DOC-SYNC-001

**Reviewer:** Claude2
**Date:** 2026-04-22
**Outcome:** Approved

## Acceptance Check

1. **`RESEARCH_BACKEND_MATURITY_MATRIX.md` matches current `Qlib` / `TRL` / `OpenClaw` evidence** ✅
   - `RESEARCH_BACKEND_MATURITY_MATRIX.md:60` now lists OpenClaw as
     `governed` / Activation-Ready with the remaining gate framed as
     repo-authoritative runtime adoption (matches `OPENCLAW_RUNTIME_CONTRACT`
     and the live gateway smoke recorded in
     `integrations/openclaw/integration.md:152`).
   - `RESEARCH_BACKEND_MATURITY_MATRIX.md:61` lists Qlib as `smoke-tested`
     with RS-003 candidate + governed-data gates as the remaining proof,
     consistent with `OSS_INTEGRATION_CHECKLIST.md:39` and
     `services/research/qlib/requirements.txt:1` (`pyqlib==0.9.6`).
   - `RESEARCH_BACKEND_MATURITY_MATRIX.md:62` lists TRL as `smoke-tested`
     with the runtime-data gates retained, matching
     `OSS_INTEGRATION_CHECKLIST.md:38`.
   - The Production-Path Tier definitions
     (`RESEARCH_BACKEND_MATURITY_MATRIX.md:47-48`) and the cross-backend
     consistency table (`RESEARCH_BACKEND_MATURITY_MATRIX.md:163-172`) now
     keep the `smoke-tested`/`governed` baseline distinct from
     production activation, so the matrix no longer collapses runnable
     baselines into "active production path".

2. **`DEFERRED_OSS_ACTIVATION_MAP.md` no longer understates landed adapters and smoke paths** ✅
   - `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md:33` updates the Qlib
     row from `criteria-defined` / no-adapter / no-smoke to `smoke-tested`
     with `pyqlib==0.9.6`, the governed `GovernedQlibDataAdapter` +
     `QlibLightGBMBackend` baseline, and the 2026-04-17 smoke evidence in
     `integrations/qlib/`.
   - The Qlib detail section
     (`services/learning/DEFERRED_OSS_ACTIVATION_MAP.md:43-86`) now lists
     adapter + smoke as Done and reframes the remaining work as the first
     governed alpha activation through the RS-003 / market-data gates.
   - The TRL section
     (`services/learning/DEFERRED_OSS_ACTIVATION_MAP.md:89-134`) is preserved
     as smoke-tested and is not silently downgraded; the lingering
     `pyqlib 0.9.1` reference is also corrected to `pyqlib 0.9.6`.

3. **No canonical OSS doc disagrees on the maturity tier for those rows** ✅
   - Cross-doc tier reading is now: OpenClaw `governed`, Qlib
     `smoke-tested`, TRL `smoke-tested` across
     `OSS_INTEGRATION_CHECKLIST.md:36-39`,
     `RESEARCH_BACKEND_MATURITY_MATRIX.md:60-62`, and
     `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md:33-34`.
   - `integrations/openclaw/integration.md`,
     `integrations/qlib/integration.md`, and
     `integrations/trl/integration.md` remain the evidence anchors and were
     not weakened to chase the older summary text.

## Review Notes

Static review only — confirmed by reading the diff between the working
tree and `a66b27a`, by re-reading the current file contents, and by
spot-checking the requirements pins. No code or smoke runs were
re-executed locally.

Non-blocking caveats (already surfaced in
`support/sidecars/OSS-003-DOC-SYNC-001/OSS-003-DOC-SYNC-001-SIDECAR-REVIEW.md`
and not re-litigated here):

- `integrations/qlib/integration.md:6` keeps the phrase
  `Status: governed runnable adapter verified`. This is adapter-boundary
  language; the checklist and matrix correctly classify the row as
  `smoke-tested`. Treat as evidence-doc wording drift.
- `services/learning/trl/requirements.txt:4` still references
  `pyqlib 0.9.1` in the compatibility comment, while the deferred map
  and matrix have moved to `pyqlib 0.9.6`. Detail-line drift outside
  this task's artifact list.
- TRL ownership wording differs across surfaces (matrix shows Qwen,
  deferred-map summary shows Claude, deferred-map detail splits
  Claude + Qwen, checklist names OSS-NEXT-002 / Claude). This is
  ownership wording drift, not maturity-tier disagreement.
- Matrix carries consistency edits for `RLlib` (now `version-pinned`)
  and `QuantLib` (now `governed` / Production Research Path) that fall
  outside the OpenClaw / Qlib / TRL trio; both are checklist-backed and
  do not contradict the parent acceptance contract.

## Follow-up

- Optional cleanup: align the wording in `integrations/qlib/integration.md`
  status header and the `services/learning/trl/requirements.txt`
  compatibility comment with the checklist in a separate hygiene slice.
  Not required for this approval.
- The repo-authoritative OpenClaw runtime adoption remains tracked under
  `PER-001-RUNTIME-INTEGRATION-001` and is not part of this doc-sync
  closure.
