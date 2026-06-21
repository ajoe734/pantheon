# AG-DES-E2E-001 — Acceptance Packet and Dependency Map

**Sidecar kind:** acceptance_packet  
**Sidecar task:** AG-DES-E2E-001-SIDECAR-ACCEPTANCE  
**Parent task:** AG-DES-E2E-001  
**Parent owner:** Claude2  
**Parent reviewer:** Claude  
**Prepared by:** Claude (sidecar owner)  
**Date:** 2026-06-21  
**Authority doc:** `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/06_winner_branch_e2e_and_isolation.md`

---

## Purpose

This packet gives the parent task owner (Claude2) a structured acceptance checklist and the reviewer (Claude) a clear gate matrix before marking the task `review_approved`. It is a support artifact only — it does not modify canonical truth or schema definitions.

---

## Scope of AG-DES-E2E-001

AG-DES-E2E-001 must freeze two canonical artifacts as test files in the Pantheon repo:

1. `services/control-plane/tests/agora/test_winner_branch_e2e_v13.py` — §F1 canonical winner-branch E2E (11 steps)
2. `services/control-plane/tests/agora/test_agora_isolation_matrix.py` — §F2–F7 isolation acceptance matrix

**Iron rule:** Frozen v1 / v1.1 / v1.2 bundle indexes and OpenAPI files must not be altered. All additions are additive under `v4/` and `agora_v1_3.openapi.yaml`.

---

## §F1 Acceptance Checklist — Winner-Branch E2E (11 Steps)

Each row maps to one step in `06_winner_branch_e2e_and_isolation.md §F1`. The test file must cover every assertion listed.

| # | Step | Key Assertions Required in Test |
|---|---|---|
| 1 | Identity and private servant | (a) Agora user has exactly one user-private servant; (b) persona class/scope asserted and no execution authority; (c) User B cannot resolve User A's servant ID |
| 2 | Create workshop with expert hypothesis | (a) Raw initial message is encrypted/private; (b) event row stores private ref + redacted summary; (c) Management cannot read raw text |
| 3 | Servant reconstruction and gap analysis | (a) Causal-chain, explicit/inferred definitions, uncertainty set, completeness snapshot, Next-Best-Question emitted; (b) typed WorkshopCards returned; (c) SSE events are ordered |
| 4 | First StrategySpec draft | (a) One Registry draft created; (b) one workshop-version link created; (c) StrategySpec truth not copied into workshop storage; (d) strategy/Registry/workshop IDs and lineage asserted |
| 5 | Research plan | (a) ResearchPlan contains all required stage types (data validation, winner-branch scoring, probability/EV, robustness/OOS, etc.); (b) Trader approval recorded before any run dispatches |
| 6 | Research execution | (a) Real vs fixture/stub labelled; (b) no silent fallback; (c) progress events ordered and replayable; (d) every output has evidence/artifact refs; (e) no order route asserted |
| 7 | Results and patch proposal | (a) VersionPatchProposal created by result-synthesis; (b) proposal validated and accepted; (c) new immutable Registry draft created |
| 8 | Compare and readiness | (a) predicted and observed results are separate; (b) OOS/cost/capacity/regime checks visible; (c) readiness transitions are evidence-based; (d) fixture/stub cannot satisfy full validation gate |
| 9 | Select execution candidate(s) | (a) Trader may select primary, multiple shadow variants, or none; (b) selection does not promote to live; (c) no RuntimeBinding created |
| 10 | Candidate pool and Trading Room | (a) CandidatePool generated with scoring recipe; (b) DashboardRecipe accepted with widget/layout version; (c) Trading Room gate checks readiness state before becoming available |
| 11 | Decision event and governed intent | (a) Entry/add/reduce/exit/review event triggered; (b) confidence/probability/EV/risk/evidence/invalidation shown; (c) TradingIntent created on approval; (d) shadow runs as no-order evaluation; (e) paper/canary/live creates request-only governed handoff; (f) **Agora creates no broker order, RuntimeBinding, or capital binding** — must be an explicit assertion |

**F1 summary gate (all must pass):**
- [ ] All 11 steps have corresponding test functions or parametrized cases
- [ ] Step 11(f): `assert_no_broker_order_or_runtime_binding()` or equivalent explicit negative assertion present
- [ ] No step references an unmerged contract path (each citation must be a merged prose or schema file)

---

## §F2–F7 Acceptance Checklist — Isolation Matrix

### F2 Cross-Repo Compatibility

| ID | Assertion | Test Required |
|---|---|---|
| XR-01 | Frontend manifest names exact backend v1.3 bundle index hash | Assert manifest `bundle_hash` == `bundle_index.v1_3.json` sha256 |
| XR-02 | Generated TypeScript records source contract commit and schema hashes | Assert `__contract_commit` and `__schema_hash` fields present |
| XR-03 | CI fails on missing required capability | Negative: remove a required capability, assert CI/test fails |
| XR-04 | CI fails on schema/OpenAPI hash drift | Negative: mutate a schema byte, assert hash mismatch error raised |
| XR-05 | `execute-plans` pages use BFF client facade; no direct API fetch | Assert no raw `fetch`/`axios` calls to `/api/agora/` in page code |
| XR-06 | Additive bundle does not modify prior frozen bundle hashes | Assert v1.2 hash embedded in v1.3 equals exact v1.2 bytes sha256 |
| XR-07 | Frontend build declares minimum compatible backend bundle version | Assert `minBackendBundle` field present in frontend manifest |

### F3 Cross-User Isolation

| ID | Assertion | Test Required |
|---|---|---|
| ISO-U01 | User A cannot list/get/update User B workshop | 403/404 on cross-user workshop operations |
| ISO-U02 | User A cannot read User B private content, cards, SSE or replay | 403/404; no content leakage |
| ISO-U03 | User A cannot read/modify User B DashboardRecipe | 403/404 |
| ISO-U04 | User A cannot read User B candidate pool, decision event or intent | 403/404 |
| ISO-U05 | Guessed IDs return 404/403 without existence leakage | Response body does not disclose whether the resource exists |
| ISO-U06 | Frontend query/cache keys include tenant + user + aggregate | Assert key format includes all three scope components |
| ISO-U07 | SSE authorization checked before connect and replay | Unauthorized SSE connect returns 401; replay with wrong user returns 403 |
| ISO-U08 | Idempotency keys are scoped by tenant/user/operation | Two users with same operation-id get independent results |

### F4 Agora vs Management Isolation

| ID | Assertion | Test Required |
|---|---|---|
| ISO-M01 | Agora user token denied all Management command routes | Assert 403 on Management command routes with Agora token |
| ISO-M02 | Management projection receives redacted workshop content only | Assert raw text absent from Management-visible projection |
| ISO-M03 | Management cannot decrypt private content through normal APIs | Assert private content ref is opaque; decrypt path requires break-glass |
| ISO-M04 | Institutional persona receives minimized/redacted ContextBundle | Assert ContextBundle fields are subset; raw private content absent |
| ISO-M05 | Agora cannot create RuntimeBinding, capital binding or broker order | Explicit negative assertion; any attempt returns 403 |
| ISO-M06 | Canary/live actions are handoff requests only | Assert action type = `governed_handoff_request`; no direct execution record |
| ISO-M07 | Break-glass access separate, audited, unavailable to ordinary Management users | Assert break-glass route requires elevated scope; audit log entry created |
| ISO-M08 | Institutional learning requires consent/privacy gates; never extends raw-content retention | Assert learning writebacks contain only redacted refs; raw text absent |

### F5 App/Build Isolation

Short-term monorepo acceptance:
- [ ] Route guards and BFF authorization both tested (hiding a menu is not security)
- [ ] Agora code does not call Management command clients (assert no Management import path)

Target-state acceptance (post dual-entry migration — mark as deferred if not yet applicable):
- [ ] Agora and Management produce separate bundles
- [ ] Agora bundle contains no Management page chunks
- [ ] Separate auth audiences and CSP
- [ ] Independent deployment manifests

### F6 Privacy and Storage

| ID | Assertion | Test Required |
|---|---|---|
| ISO-P01 | Raw workshop text absent from DB rows/logs/traces/audit | Assert storage rows contain only private ref, not raw text |
| ISO-P02 | Private object refs are opaque | Assert ref is a UUID/hash, not a path or plaintext |
| ISO-P03 | Owner-only decrypt is audited | Assert audit log entry created on decrypt |
| ISO-P04 | Retention/expiry/delete behavior tested | Assert expired content returns 404 or empty; delete is final |
| ISO-P05 | Redaction failure is fail-closed | Assert service returns error/empty (not raw content) on redaction failure |
| ISO-P06 | Central personas cannot receive raw private prompt by default | Assert ContextBundle for central persona contains no raw prompt |

### F7 Event and Concurrency Acceptance

| ID | Assertion | Test Required |
|---|---|---|
| EV-01 | Per-workshop `sequence_no` is monotonic | Assert sequence_no increments with each event |
| EV-02 | Delivery is at-least-once and client dedupes | Assert duplicate delivery handled idempotently |
| EV-03 | Last-Event-ID replay works in support window | Assert replay returns events from Last-Event-ID onward |
| EV-04 | Replay gap returns `SSE_REPLAY_UNAVAILABLE` | Assert correct error code on out-of-window replay |
| EV-05 | Stale If-Match returns 409 and has no side effect | Assert 409 returned; resource unchanged |
| EV-06 | Repeated Idempotency-Key returns prior command result | Assert identical response on repeat; no duplicate side effect |
| EV-07 | First persisted message acknowledgement meets p95 <2s target | Record observed latency; mark as evidence (not blocking if infra unavailable) |

---

## Dependency Map

### What AG-DES-E2E-001 Depends On (Contract Inputs)

The test files for this task must cite **merged** contract paths only. Before the tests are reviewable, these must exist in the repo:

| Dependency | Merged artifact required | Status |
|---|---|---|
| v1.3 OpenAPI | `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Owned by AG-XR-OPENAPI-004 |
| v1.3 bundle index | `services/control-plane/specs/agora/bundle_index.v1_3.json` | Owned by AG-XR-OPENAPI-004 |
| v4 schemas | `services/control-plane/specs/agora/v4/*.schema.json` | Owned by AG-DES-VERS-001 / AG-DES-RS-001 / AG-DES-SSE-001 / AG-DES-TR-001 / AG-DES-CARD-001 |
| Workshop SSE typed contract | `docs/.../03_workshop_sse_contract.md` | Design doc already merged in design-closure-round2 |
| Trading Room / governed intent | `docs/.../04_trading_room_and_governed_intent.md` | Design doc already merged |
| Strategy versioning / readiness | `docs/.../01_strategy_versioning_patch_readiness.md` | Design doc already merged |

> **Note for owner:** If schema files under `v4/` are not yet in the repo when tests are being written, use `pytest.importorskip` or skip markers with an explicit blocker ref. Do not write tests against stubs that could satisfy assertions the real contract would fail.

### What Depends On AG-DES-E2E-001 (Downstream Unblocks)

| Downstream task | Unblocked when |
|---|---|
| AG-E2E-SW-001 | E2E steps and isolation matrix merged |
| AG-E2E-TR-001 | Trading Room E2E assertions merged |
| AG-TEST-ID-001 | Isolation matrix merged |

---

## Review Gate Summary (for Claude as reviewer)

When Claude receives AG-DES-E2E-001 for review, verify:

1. **Frozen artifact integrity** — Run `sha256sum` on `bundle_index.json`, `bundle_index.v1_1.json`, `bundle_index.v1_2.json`, `agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`, `agora_v1_2.openapi.yaml` and confirm they match the values recorded in `bundle_index.v1_3.json`'s `extends_hash` field. Any mutation is a blocking defect.

2. **F1 completeness** — All 11 steps have test coverage. Step 11 contains an explicit assertion that Agora creates no broker order, RuntimeBinding, or capital binding.

3. **Isolation matrix coverage** — All F2–F7 IDs have corresponding test cases. F5 target-state items may be marked `deferred` with a comment citing the dual-entry migration task.

4. **No self-invented fields or routes** — Test assertions reference only fields defined in the merged v4 schemas or the design-closure-round2 prose docs. Any field not in a merged schema is a blocking defect.

5. **No silent fixtures** — Any use of stub/fixture data in tests must be clearly labelled; the test must fail if the real implementation is absent.

6. **Contract citation** — Each test file header cites the merged contract path(s) it validates, not the brief or this sidecar packet.

---

## Files This Packet Does NOT Modify

- `services/control-plane/specs/agora/bundle_index.json` (frozen)
- `services/control-plane/specs/agora/bundle_index.v1_1.json` (frozen)
- `services/control-plane/specs/agora/bundle_index.v1_2.json` (frozen)
- `services/control-plane/openapi/agora_v1.openapi.yaml` (frozen)
- `services/control-plane/openapi/agora_v1_1.openapi.yaml` (frozen)
- `services/control-plane/openapi/agora_v1_2.openapi.yaml` (frozen)
- Any L1 canonical truth docs

---

## Handoff Destination

When AG-DES-E2E-001 is submitted for review, this packet should be cited in the `review` handoff message so the reviewer (Claude) can use it as the gate checklist. The parent task owner (Claude2) is also the sidecar reviewer who will approve this sidecar packet.
