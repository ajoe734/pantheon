# APP-002-W5-LOVABLE-CUTOVER — Acceptance Packet & Dependency Map

**Sidecar Task ID**: `APP-002-W5-LOVABLE-CUTOVER-SIDECAR-ACCEPTANCE`
**Parent Task**: `APP-002-W5-LOVABLE-CUTOVER`
**Parent Owner**: Copilot
**Parent Reviewer**: Qwen
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Qwen
**Helper Kind**: `acceptance_packet`
**Date**: 2026-04-12

> This is a **support artifact only**. It does not modify canonical truth, core contracts, or runtime/registry/governance implementations. It provides an acceptance checklist and dependency map so the parent owner can complete the production cutover without re-deriving context.

---

**1. Parent Task Summary**

| Field | Value |
|-------|-------|
| **ID** | `APP-002-W5-LOVABLE-CUTOVER` |
| **Title** | Finalize Pantheon-to-Lovable production cutover |
| **Phase** | Phase 5: APP-002 Execution Wave 5 |
| **Owner** | Copilot |
| **Reviewer** | Qwen |
| **Status** | `todo` |
| **Summary (zh)** | 完成 front repo BFF cutover、驗證 handoff packet 與 Lovable prompt packet 對應 live Pantheon contract。 |

**1.1 Parent Acceptance Criteria**

| # | Criterion | Key Question |
|---|-----------|-------------|
| AC-1 | `front_repo_uses_pantheon_bff` | Does `front-ai-trading-system` point to Pantheon BFF defaults (no legacy endpoints) and refresh generated client/types/hooks? |
| AC-2 | `lovable_packets_match_live_contracts` | Do Lovable prompt packets + coordination YAML match the live Pantheon contract and example payloads? |
| AC-3 | `production_cutover_verified` | Is the end-to-end production cutover validated (front repo + Lovable handoff + live contract alignment)? |

---

**2. Dependency Map**

**2.1 Direct Dependency (from ai-status)**

| Dependency | Status | Summary | Artifacts |
|------------|--------|---------|-----------|
| `APP-002-W5-SSE-LIVE` | **done** | SSE endpoints + reconnect/replay + frontend reconciliation aligned | `services/control-plane/bff/main.py`, `services/frontend/sse_reconciler.py`, `services/frontend/adapter.py` |

**2.2 Critical Wave Prerequisites (already done)**

| Task | Status | Why It Matters |
|------|--------|----------------|
| `APP-002-W1-FRONT-HANDOFF` | **done** | Contract-ready + Lovable prompt + handoff bundle for F-042 exist and are mirrored to front repo | 
| `APP-002-W4-PERSONA-MGMT` | **done** | Composed view is live and backend-shaped (no demo providers) |
| `APP-002-W4-REMAINING-CATALOG` | **done** | All remaining canonical read surfaces live; contract and examples converged |
| `APP-002-W2-CLI-FALLBACK` | **done** | Secondary control path is usable; non-blocking but supports operator safety |

**2.3 External Dependencies (manual verification required)**

| Dependency | Owner | Notes |
|------------|-------|-------|
| `front-ai-trading-system` repo | Copilot | Required to confirm BFF base URL cutover + generated client refresh |
| Lovable project `140c41d5-9cd8-4d6b-ba02-66d5941d0dbe` | Copilot (human-triggered) | Prompt packet must be submitted by a human and produce `ui-done` handoff |

---

**3. Current Artifact Inventory (Pantheon-side)**

**3.1 Coordination Responses (present in repo)**

| File | Status | Purpose |
|------|--------|---------|
| `.coordination/responses/F-042-contract-ready.yaml` | ✅ Present | Declares contract-ready handoff and references BFF/spec/example/prompt artifacts |
| `.coordination/responses/F-042-lovable-ui-task.yaml` | ✅ Present | Lovable UI task packet with constraints + allowed endpoints |
| `.coordination/responses/F-042-lovable-prompt.md` | ✅ Present | Human-readable Lovable prompt packet |

**Notes**

- `F-042-lovable-ui-task.yaml` currently has `links.bff_spec_path: null` and `links.example_payload_paths: []`. Prior W1 handoff review notes this is acceptable because the mirrored front repo packet fills these fields.
- `F-042-contract-ready.yaml` marks `front_repo_receives_handoff_bundle: true`, but the actual front repo mirror must be verified in the external repo.

**3.2 Canonical Guidance for Cutover**

| File | Status | Purpose |
|------|--------|---------|
| `docs/delivery-coordination-bus.md` | ✅ Present | Defines Lovable lane and GitHub coordination policy |
| `.coordination/README.md` | ✅ Present | Defines coordination payload types and expectations |
| `docs/examples/F-042-review-page.json` | ✅ Present | Example payload for F-042 Promotion Review page |

---

**4. Acceptance Checklist for Parent Owner (Copilot)**

**AC-1: `front_repo_uses_pantheon_bff`**

- [ ] Confirm `front-ai-trading-system` BFF base URL points to Pantheon defaults (no legacy endpoints).
- [ ] Refresh generated client/types/hooks so the F-042 page consumes Pantheon-shaped fields.
- [ ] Verify front repo uses the shared BFF client (no raw fetch additions).
- [ ] Record the cutover commit or coordination note in the front repo handoff thread.

**AC-2: `lovable_packets_match_live_contracts`**

- [ ] Confirm `F-042-contract-ready.yaml` references the correct live contract and example payload paths.
- [ ] Validate `docs/examples/F-042-review-page.json` against live response from `GET /api/v1/operator/deployment-review/{plan_id}`.
- [ ] Confirm Lovable prompt constraints match the current delivery-bus policy (BFF client only, no raw fetch, no demo providers).
- [ ] Ensure allowed endpoints in Lovable packet remain limited to `GET /api/v1/operator/deployment-review/{plan_id}` and `POST /api/v1/operator/commands`.

**AC-3: `production_cutover_verified`**

- [ ] Trigger Lovable task from the prompt packet and confirm it emits `.coordination/requests/F-042-ui-done.yaml` in the front repo.
- [ ] Verify the UI references Pantheon BFF endpoints and uses backend-shaped fields for approval/governance decisions.
- [ ] Run any front repo integration checks or Pantheon BFF smoke tests (`services/control-plane/bff/smoke_test.py`) to confirm live compatibility.
- [ ] Confirm no legacy endpoints remain referenced in the front repo or Lovable output.

---

**5. Risk & Blocker Assessment**

| Risk | Severity | Mitigation |
|------|----------|------------|
| Front repo checkout not present in this workspace | **Medium** | Requires manual validation in `front-ai-trading-system` repo; cannot be fully verified from Pantheon repo alone |
| Lovable task is human-triggered | **Medium** | Explicitly schedule a human run; ensure `ui-done` handoff appears in `.coordination/requests/` |
| Lovable packets reference null `bff_spec_path` / empty example list on Pantheon side | **Low** | Acceptable if mirrored front repo packet fills paths; re-check mirrored copy |
| Legacy BFF base URL still referenced in front repo | **High** | Search and replace legacy base URL; confirm deployment config uses Pantheon defaults |

---

**6. Recommended Execution Plan (Parent Owner)**

```
Phase 1: Front Repo Cutover
  1a. Sync front-ai-trading-system repo and confirm .coordination mirror files
  1b. Update BFF base URL defaults to Pantheon
  1c. Refresh generated client/types/hooks
  1d. Remove any legacy endpoint references

Phase 2: Lovable Packet Validation
  2a. Cross-check F-042 contract-ready + prompt packet vs live BFF responses
  2b. Launch Lovable task via human prompt
  2c. Verify ui-done handoff payload

Phase 3: Production Verification
  3a. Run BFF smoke tests or live API checks
  3b. Confirm UI renders approval/governance outcomes from backend-shaped fields
  3c. Record cutover evidence in handoff / review notes
```

---

**7. Verification & Handoff**

**7.1 Verification Snapshot (Pantheon repo)**

| Field | Value |
|-------|-------|
| **Verified by** | Codex (sidecar owner) |
| **Verification date** | 2026-04-12 |
| **Pantheon-side artifacts** | `.coordination/responses/*`, `docs/examples/F-042-review-page.json`, `docs/delivery-coordination-bus.md`, `.coordination/README.md` all present |
| **Front repo cutover** | **Not verified** (external repo required) |
| **Lovable execution** | **Not verified** (human-triggered) |

**7.2 AC Status at Snapshot**

| AC | Status | Evidence |
|----|--------|----------|
| AC-1 `front_repo_uses_pantheon_bff` | ⬜ Pending | Requires front repo validation |
| AC-2 `lovable_packets_match_live_contracts` | ⚠️ Partial | Pantheon packets present; live contract check + front mirror needed |
| AC-3 `production_cutover_verified` | ⬜ Pending | Requires Lovable run + UI-done handoff + live validation |

**7.3 Handoff**

- **To**: Qwen (sidecar reviewer)
- **From**: Codex (sidecar owner)
- **Message**: Acceptance packet prepared with dependency map and cutover checklist. Pantheon-side artifacts verified present; external front repo + Lovable execution remain pending. Please review and approve.
- **Review outcome**: Approved by Qwen on 2026-04-12. Ready for owner finalize.

---

*Generated by Codex as sidecar acceptance packet for APP-002-W5-LOVABLE-CUTOVER. This is a support artifact — it does not modify canonical truth.*
