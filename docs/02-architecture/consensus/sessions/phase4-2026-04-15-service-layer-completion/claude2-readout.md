# Claude2 Readout — Phase 4: Service Layer Completion (Round 1)

## Lane

- Agent: Claude2
- Capability focus: independent control-plane / governance-review second opinion. Verify Claude's cited code claims against current repo state, audit the proposed slicing against L1 policy docs that have been thinly cited so far (BFF_HA, CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY), and surface compliance gaps.

## Canonical Sources Read

- L0: `ai-status.json`, `current-work.md`
- L1: `TARGET_ARCHITECTURE.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- L2: `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`, `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`
- Session: `phase2-phase6-gap-inventory.md`, `starter-draft.md`, `claude-readout.md`, `codex-readout.md`, `gemini-readout.md`, `qwen-readout.md` (Claude-covered), `copilot-readout.md` (Codex-covered), `consensus-packet.md`, `planning-session.json`
- Repo direct: `services/control-plane/bff/command_executor.py`, `services/control-plane/bff/read_store.py`, `services/control_plane/internal_api.py`

---

## 1. Lane Posture

I am the second governance-review voice. Claude (facilitator) has already written a complete cited readout. To avoid duplicating work, this readout focuses on three things Claude's readout does not cover:

1. Independent verification that Claude's headline cited observations are still true against current code (some are not).
2. L1 policy compliance audit against `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` and `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`, which are in the brief set but only thinly cited.
3. Slicing additions that follow from those compliance findings.

I do not rewrite `starter-draft.md`. Recommendations below are additive input for Codex (baton owner) when refining the draft, and for the eventual consensus packet.

---

## 2. Verification Findings — Where Earlier Readouts Have Drifted

### VFY-01: Evolution-command dispatch is no longer a local placeholder

`claude-readout.md` OBS-01 and `qwen-readout.md` Risk 1 / `Q3` both assert that `command_executor.py` `_execute_approve_evolution_decision` and `_execute_evolution_action` "record decision locally" with no HTTP dispatch. This was true at some prior commit; it is no longer true on the current branch.

Current state (`services/control-plane/bff/command_executor.py:420-499`):

- `_execute_approve_evolution_decision` constructs `url = _governance_url(f"/api/evolution/proposals/{decision_id}/{approval_action}")` and calls `_post_json(url, payload, ...)`.
- `_execute_evolution_action` constructs `url = _governance_url(f"/api/evolution/proposals/{decision_id}/execute")` and dispatches the same way.
- `_governance_url` (`:46-51`) requires `PANTHEON_GOVERNANCE_API_URL` (with `PANTHEON_EVOLUTION_API_URL` as a documented fallback) and raises `RuntimeError("Command backend is unconfigured…")` if neither is set. There is no silent fallback.
- `_actor_context` (`:77-108`) hard-fails if `actor_id` or `actor_role` are missing — so the evolution command path is enforcing governance contract at the BFF boundary, not deferring it.

What this changes for the plan: the open question is no longer "where do evolution endpoints live in the *client*?" — that has already been decided in code, and they live behind `PANTHEON_GOVERNANCE_API_URL`. The remaining gap is that there is no **server** answering at that URL. `SVC-GOVERNANCE-API` therefore has a stronger acceptance criterion than the starter-draft currently states: it must produce a service that responds to the four already-baked URL contours (`POST /api/evolution/proposals/{id}/approve|reject`, `POST /api/evolution/proposals/{id}/execute`, plus the mutation review pair at `:508-561`).

**Cite**: `services/control-plane/bff/command_executor.py:46-51, 420-499, 508-561, 593-597`

### VFY-02: BFF default-seed fallback is policy-prohibited but still in code

`claude-readout.md` OBS-03 cites `read_store.py:43-175` for "snapshot → default seed fallback." The `CanonicalSnapshotAdapter` docstring at `:86-92` has since been updated to say:

> "When canonical snapshot files are available, the read surfaces prefer them. When they are absent, **the normal integration path must surface backend unavailability explicitly instead of silently inventing local defaults.**"

That matches `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §5.1`:

> "BFF 正常整合路徑不得以本地 seed、snapshot、或隱性 localhost backend 預設假裝 backend 已就緒"

However, `_default_read_data()` still exists and is still used:

- `services/control-plane/bff/read_store.py:582` — function still defined.
- `services/control-plane/bff/read_store.py:4362-4369` — `ReadSurfaceStore` still calls `_default_read_data()` to populate `self._data` when no snapshot/store path resolves.
- Tests `test_tw02_parameter_controls_contract.py:15`, `test_cw04_redteam_memo_contract.py:15`, `test_tw04_teaching_replay_contract.py:14`, and `test_read_store_bootstrap_snapshot.py:12` import `_default_read_data` and depend on its current shape.

The state therefore is: **`CanonicalSnapshotAdapter` has been hardened to refuse silent defaults, but the older `ReadSurfaceStore` path still seeds defaults and four contract tests still exercise that path.** This is a policy/code drift that the current `SVC-SURFACES` acceptance language ("BFF no longer depends on snapshot/default fallback as the normal integration path") does not yet operationalize.

What this changes for the plan: `SVC-SURFACES` cannot mark "fallback removed" by deleting one function — it must also migrate the four dependent contract tests onto a service-backed bootstrap, or explicitly reclassify them as "test-only seed" with a guard that prevents the seed code from being reachable in production builds.

**Cite**: `services/control-plane/bff/read_store.py:86-92, 582, 4362-4369`; `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §5.1`; `test_tw02_parameter_controls_contract.py:15`, `test_cw04_redteam_memo_contract.py:15`, `test_tw04_teaching_replay_contract.py:14`, `test_read_store_bootstrap_snapshot.py:12`

### VFY-03: Flask-vs-FastAPI claim still holds

`claude-readout.md` OBS-04 cites `services/control_plane/internal_api.py:12` for Flask. Verified: `:11` imports `from flask import Flask, request, jsonify` and `:21` instantiates `app = Flask(__name__)`. The lazy-import file path described in OBS-02 also matches `:27-56`. These two observations are still accurate and remain blocking for `SVC-RUNTIME-CONTROL` Dockerization.

**Cite**: `services/control_plane/internal_api.py:11-21, 27-56, 82-119`

---

## 3. L1 Policy Compliance Findings

### POL-01: BFF_HA multi-replica requirement not yet reflected in `SVC-SURFACES`

`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §3.2` states "至少 2 replicas." `§3.4` requires shared backing store for session/cursor/cache when shared. The current `SVC-SURFACES` acceptance criteria in `planning-session.json` say only "BFF and feedback are packaged and runnable in the target stack" — that is satisfied by a single instance.

The single-VM test profile is allowed to relax this for the test stack, but `SVC-BASELINE` should explicitly record the relaxation rather than ignore the policy. Otherwise the test compose ships a contract violation that will resurface at the dual-VM production cut.

**Recommended addition**: `SVC-BASELINE` should declare "single-VM test profile runs BFF as 1 replica with `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §3.2` deferred to dual-VM profile" as an explicit deferral note, not silent.

**Cite**: `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §3.2, §3.4, §8.2`; `planning-session.json` `SVC-SURFACES.acceptance`

### POL-02: BFF_HA backup-control-path requirement intersects with `SVC-RUNTIME-CONTROL`

`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6` requires that `pause`, `rollback`, `kill switch`, and `health diagnostics` remain available through a non-BFF path (admin CLI / control-plane internal API / runtime-manager protected admin endpoint) even when BFF is fully down. `§8.5` says "BFF 不可成為 kill-switch 唯一路徑."

Today, `internal_api.py` is exactly that backup path (kill-switch dispatched through `KillSwitchController` per `:44-75`). When `SVC-RUNTIME-CONTROL` packages `internal_api.py` into a container, the policy compliance check is: can an operator still reach the kill-switch endpoint without going through BFF? In a compose stack that requires this, runtime-control must expose its `/api/internal/v1/kill-switch` route on a port reachable from outside the BFF service (or via a dedicated admin network), and the smoke test must demonstrate this.

**Recommended addition**: `SVC-RUNTIME-CONTROL` acceptance must require "kill-switch reachable from a non-BFF client (e.g., curl from host) in the compose stack" — not only "live kill-switch invocation through the container" as Claude proposed.

**Cite**: `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6, §8.5`; `services/control_plane/internal_api.py:44-75`

### POL-03: Saga orchestration is missing from current slicing

`CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §12` explicitly anchors the L1 contract to:

- `services/control-plane/governance/deployment_saga.contract.md`
- `services/control-plane/governance/deployment_saga.py`

These are two of the artifacts named in `README.md` Group A (`promotion-svc → deployment_saga.py`). The current `SVC-GOVERNANCE-API` slice says "expose approval/deployment/binding/evolution APIs" but does not call out:

- the saga orchestrator's ownership of retry/compensation per `§7.2`,
- the requirement to preserve explicit intermediate states (`approved_not_deployed`, `deployment_failed`, `binding_pending`, `runtime_load_failed`) per `§2.3`,
- the outbox/inbox plus dedup-table requirement per `§9`.

If `SVC-GOVERNANCE-API` ships only CRUD-style read/write endpoints over the domain objects without preserving the saga state machine, the BFF view of "deployment in progress" will not match the saga truth, and the smoke path cannot exercise `§6` failure scenarios.

**Recommended addition**: `SVC-GOVERNANCE-API` should be split — or its acceptance language tightened — so that one slice covers approval/binding/plan CRUD and a second covers deployment-saga orchestrator endpoints with explicit intermediate-state semantics. At minimum, the acceptance criteria must require: (a) saga state machine reflected in API responses; (b) outbox events emitted on state transitions; (c) at-least-once + idempotent consumer pattern documented.

**Cite**: `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §2.3, §5, §6, §7.2, §9, §12`; `README.md` Group A; `planning-session.json` `SVC-GOVERNANCE-API.artifacts`

### POL-04: Compose smoke acceptance must cover saga compensation, not only happy path

`CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §6` enumerates three canonical failure cases the policy says must be visible: artifact-approved-but-binding-failed (`§6.1`), binding-created-but-runtime-load-failed (`§6.2`), rollback-mid-flight-failed (`§6.3`). `SVC-COMPOSE` acceptance currently says "single-VM compose stack boots" and "smoke commands and dependency wiring are documented and repeatable."

A boot-only smoke is not a saga smoke. Without one explicit failure-injection scenario, the compose stack can satisfy `SVC-COMPOSE` while masking exactly the consistency class the L1 policy was written to cover.

**Recommended addition**: `SVC-COMPOSE` smoke command set should include at least one saga-compensation scenario (e.g., simulated runtime-load failure → assert `binding_created_but_inactive` is visible through governance-api read path). This can be marked as `optional/profile=saga-smoke` so it does not gate the default core boot, but it has to exist as a callable command, not a future TODO.

**Cite**: `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §6.1, §6.2, §6.3`; `planning-session.json` `SVC-COMPOSE.acceptance`

---

## 4. Positions on the Open Disagreements (independent vote)

### Q1 — `internal_api.py` as long-lived runtime-control vs temporary adapter?

Agree with Claude's position: package as-is. Adding: the FastAPI migration follow-on should be tracked as a downstream task with an explicit dependency on POL-02 acceptance — i.e., before migration, the non-BFF backup-control-path contract must be re-tested in the FastAPI rewrite. Otherwise the rewrite will quietly break the kill-switch backup path.

### Q2 — Where do evolution endpoints live?

Agree they belong in `governance-api`, but the framing changes given VFY-01: this is no longer a contract-design question, it is a server-implementation question. `command_executor.py` already targets `/api/evolution/proposals/{id}/(approve|reject|execute)`. `SVC-GOVERNANCE-API` must implement those exact route shapes — not invent new ones — or `command_executor.py` will need to be re-cut. Spec the routes from the existing client, not vice versa.

### Q3 — BFF rewiring same wave as Dockerization?

Agree, with the qualifier from VFY-02: "rewiring" is more than swapping `CanonicalSnapshotAdapter`. It also covers the four contract tests that depend on `_default_read_data()`. If those tests cannot be migrated in the same wave, the acceptance criterion should explicitly accept "production code path uses service-backed read; test-only seed path is gated behind `PANTHEON_BFF_ALLOW_TEST_SEED=1` and is unreachable when that env is unset."

### Q4 — `web` and `cron` in default profile?

Agree with Claude that both stay out of default. Adding from `copilot-readout.md` (Codex-covered): the `core` vs `optional` profile distinction needs to be a `SVC-BASELINE` deliverable with `cron` explicitly declared as a workflow runner that is *not* a parallel truth source — this matches `codex-readout.md` Working Interpretation.

---

## 5. Risk Synthesis (delta to Claude's risk table)

| Risk | Source | Severity | Resolution slot |
|------|--------|----------|-----------------|
| Stale citation: evolution placeholder claim is no longer code-truth | VFY-01, `command_executor.py:420-499` | Medium (correctness of plan) | Update `starter-draft.md` Q2 framing; respec `SVC-GOVERNANCE-API` to match the four already-baked URLs |
| Policy/code drift: BFF default-seed still in code despite policy ban | VFY-02, `read_store.py:582, 4362-4369`, `BFF_HA §5.1` | High | `SVC-SURFACES` acceptance must address `_default_read_data()` and four dependent tests |
| BFF_HA multi-replica deferral not declared | POL-01, `BFF_HA §3.2` | Low (test stack), Medium (production cut) | `SVC-BASELINE` records explicit deferral note |
| Backup-control-path reachability not in `SVC-RUNTIME-CONTROL` acceptance | POL-02, `BFF_HA §6, §8.5` | High (compliance) | `SVC-RUNTIME-CONTROL` adds non-BFF kill-switch reachability test |
| Saga orchestration absent from slicing | POL-03, `CROSS_SERVICE_CONSISTENCY §12, §2.3, §5, §9` | High | `SVC-GOVERNANCE-API` acceptance hardened, or split into governance-API + deployment-saga slices |
| Compose smoke is boot-only; doesn't exercise saga compensation | POL-04, `CROSS_SERVICE_CONSISTENCY §6` | Medium | `SVC-COMPOSE` adds at least one compensation-scenario smoke command |

---

## 6. What the Consensus Packet Needs From This Readout

1. **Correct the framing of evolution-endpoint placement.** It is now a server-implementation slice, not an open architectural question. The four URL contours are already canonical because the BFF client speaks them.
2. **Add `_default_read_data()` migration / fence to `SVC-SURFACES` acceptance.** The four contract tests are concrete, not hypothetical.
3. **Add saga-state and outbox/inbox requirements to `SVC-GOVERNANCE-API` acceptance** (or accept that the slice will deliver an L1-incomplete API).
4. **Add a non-BFF kill-switch reachability test to `SVC-RUNTIME-CONTROL` acceptance** to discharge `BFF_HA §6, §8.5`.
5. **Add at least one saga-compensation smoke command to `SVC-COMPOSE` acceptance**, even if profile-gated.
6. **Record the `BFF_HA §3.2` multi-replica deferral as an explicit `SVC-BASELINE` note**, not silent.

I do not block consensus on items 1, 4, 5, 6 — they are tightening, not redirection. Item 2 and item 3 are higher-impact: shipping the wave without addressing them would let `SVC-SURFACES` and `SVC-GOVERNANCE-API` claim closure while the underlying contract is still unmet.

---

## Citations

- [V1] `services/control-plane/bff/command_executor.py:46-51` — `_governance_url` requires `PANTHEON_GOVERNANCE_API_URL` (or `PANTHEON_EVOLUTION_API_URL` fallback) with no silent default
- [V2] `services/control-plane/bff/command_executor.py:420-457` — `_execute_approve_evolution_decision` already POSTs to `/api/evolution/proposals/{id}/{approve|reject}`
- [V3] `services/control-plane/bff/command_executor.py:460-499` — `_execute_evolution_action` already POSTs to `/api/evolution/proposals/{id}/execute`
- [V4] `services/control-plane/bff/command_executor.py:508-561` — `ApproveMutation` and `RejectMutation` also dispatch to `/api/evolution/proposals/{id}/approve|reject`
- [V5] `services/control-plane/bff/command_executor.py:593-597` — command dispatch table wires evolution commands to the governance-targeting executors
- [V6] `services/control-plane/bff/read_store.py:86-92` — `CanonicalSnapshotAdapter` docstring explicitly forbids inventing local defaults
- [V7] `services/control-plane/bff/read_store.py:582` — `_default_read_data()` still defined
- [V8] `services/control-plane/bff/read_store.py:4362-4369` — `ReadSurfaceStore.__init__` still calls `_default_read_data()` when no store resolves
- [V9] `services/control-plane/bff/test_tw02_parameter_controls_contract.py:15`, `test_cw04_redteam_memo_contract.py:15`, `test_tw04_teaching_replay_contract.py:14`, `test_read_store_bootstrap_snapshot.py:12` — four contract tests import `_default_read_data` and depend on its shape
- [V10] `services/control_plane/internal_api.py:11-21` — Flask import and app instantiation
- [V11] `services/control_plane/internal_api.py:27-56` — lazy file-path import of `kill_switch_controller`
- [P1] `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §3.2` — at-least-2 replicas
- [P2] `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §5.1` — BFF normal path must not silently use seed/snapshot/implicit fallback
- [P3] `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6, §8.5` — non-BFF backup control path required; BFF cannot be the sole kill-switch path
- [P4] `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §2.3` — explicit intermediate-state preservation
- [P5] `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §5, §6.1-§6.3` — saga state machine and three canonical failure scenarios
- [P6] `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §7.2` — orchestrator ownership of retry/compensation
- [P7] `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §9` — outbox/inbox required fields
- [P8] `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md §12` — implementation anchor at `services/control-plane/governance/deployment_saga.{contract.md,py}`
- [S1] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/claude-readout.md` — facilitator readout, OBS-01 through OBS-07 and Q1-Q4 positions
- [S2] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/codex-readout.md` — repo-evidence anchored slicing
- [S3] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/gemini-readout.md` — port map and resource limits
- [S4] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/qwen-readout.md` (Claude-covered) — schema/contract review
- [S5] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/copilot-readout.md` (Codex-covered) — acceptance/external-dependency review
- [S6] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md` — cross-phase residual gap framing
- [S7] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json` — `SVC-*` acceptance criteria as currently proposed
