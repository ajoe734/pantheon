# SVC-POLICY-LEARNING-BOUNDARY Acceptance Packet and Dependency Map

**Sidecar Task ID**: `SVC-POLICY-LEARNING-BOUNDARY-SIDECAR-ACCEPTANCE`
**Parent Task**: `SVC-POLICY-LEARNING-BOUNDARY`
**Parent Owner (current)**: `Codex2` (auto-reassigned 2026-04-28 from Claude2 after authentication failure)
**Parent Reviewer**: `Codex`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Copilot`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-04-28

> This is a support artifact only. It does not update canonical truth, L1
> policy, core service contracts, runtime/registry/governance code, compose
> wiring, or any production learning adapter. The parent owner decides whether
> and how to absorb this packet into the main
> `SVC-POLICY-LEARNING-BOUNDARY` implementation and closeout.

---

## 1. Scope Snapshot

`SVC-POLICY-LEARNING-BOUNDARY` is a future-state service-boundary task. Its
purpose is to create a deployable `policy-learning-svc` boundary and a
non-production stub HTTP surface for future policy-learning job lifecycle
work.

The key negative requirement is as important as the positive surface:
Qlib, TRL, RL, RLlib/Ray Tune, FinRL, and W&B production activation must stay
out of scope and disabled by default. This task should provide a safe queue /
proposal / rejection boundary, not a route into governed training or registry
promotion.

Current parent task state in `ai-status.json`:

| Field | Value |
|---|---|
| `status` | `todo` |
| `owner` | `Codex2` |
| `reviewer` | `Codex` |
| `phase` | Future-State Service Materialization |
| `depends_on` | `SVC-COMPOSE` |
| Listed artifacts | `services/policy-learning/`, `services/learning/`, `docker-compose.yml` |

This packet does not touch those parent artifacts. It only maps the expected
acceptance surface and dependency boundaries for the implementation slice.

---

## 2. Acceptance Checklist

| Parent acceptance item | Sidecar mapping | Status |
|---|---|---|
| Policy-learning service exposes health, capability list, job proposal, status, and rejection APIs. | Recommended non-canonical surface is listed in §3. The shape should remain stub-only and explicitly report disabled production adapters. | MAPPED (parent must implement) |
| Dockerfile, env storage, and compose wiring are added. | Compose precedent is `consultation-svc` 8096, `source-ingest` 8097, `search-svc` 8098, and `training-session-svc` 8099: Dockerfile, `PORT`, service-owned data dir, named volume, host port env var, `/health` healthcheck. Parent owns final service name and port selection; nearby ports are already occupied. | PATTERN DOCUMENTED |
| Qlib, TRL, RL, and W&B production activation remain out of scope and disabled by default. | Existing gate docs and selectors already show this boundary: `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`, `services/learning/rl/RL_PATH_APPROVAL_GATE.md`, Qlib/TRL activation criteria, FinRL/RLlib deferred selectors, and W&B activation criteria. `policy-learning-svc` must not bypass those gates. | BOUNDARY STATED |
| Tests prove disabled production adapters are rejected and stub lifecycle is replayable. | Test surface is listed in §6. Required cases include unsupported adapter rejection, default-stub behavior, deterministic job replay/status, rejection record persistence, and compose config validation. | LISTED |
| Support-only sidecar constraint is respected. | This sidecar creates only `support/sidecars/SVC-POLICY-LEARNING-BOUNDARY/SVC-POLICY-LEARNING-BOUNDARY-SIDECAR-ACCEPTANCE.md`. | PASS |

---

## 3. Proposed HTTP Surface (derived, non-canonical)

The parent owner owns the final route names. This surface is deliberately
minimal and non-activating.

| Route | Direction | Notes |
|---|---|---|
| `GET /health` | infra | Match the Future-State Service Materialization services already using `/health` in compose. Include service name, status, data dir, and disabled-adapter summary. |
| `GET /api/policy-learning/capabilities` | read | Return allowed modes such as `stub_proposal`, plus disabled adapter entries for Qlib, TRL, FinRL, RLlib/Ray Tune, and W&B with gate references/reasons. |
| `POST /api/policy-learning/jobs` | command | Create a stub-only job proposal. Should accept requested adapter/mode, strategy/persona refs, evidence refs, and idempotency key if parent adopts one. Production adapter requests are rejected by default. |
| `GET /api/policy-learning/jobs` | read | List job proposals with status filters (`proposed`, `rejected`, `stub_completed`). |
| `GET /api/policy-learning/jobs/{job_id}` | read | Return one job proposal/status record and replay metadata. |
| `POST /api/policy-learning/jobs/{job_id}/reject` | command | Persist an explicit rejection decision, including `reason_code`, `actor_id`, and optional review note. |
| `GET /api/policy-learning/replays/{job_id}` | read | Return a deterministic replay view of the stub lifecycle, including proposal, rejection/completion state, and artifact placeholders. |

Recommended status vocabulary:

| Status | Meaning |
|---|---|
| `proposed` | Stub proposal accepted for tracking only. |
| `rejected` | Request rejected by policy boundary or explicit reviewer/operator action. |
| `stub_completed` | Stub lifecycle completed without producing a production artifact. |
| `activation_blocked` | Requested adapter/mode is recognized but blocked by existing activation gates. |

No route in this surface should call the Qlib, TRL, FinRL, RLlib/Ray Tune, or
W&B production backend, write registry promotion state, or dispatch paper/live
runtime changes.

---

## 4. Disabled Adapter Boundary

The parent implementation should treat the existing learning/research adapter
assets as inputs to the capability explanation, not as production dependencies
to invoke.

| Adapter / backend | Existing repo truth | Boundary for `policy-learning-svc` |
|---|---|---|
| Qlib | Runnable research adapter and smoke baseline exist; production activation still gated by target StrategySpec, governed market data, and activation packet criteria. | Capability may be listed as `activation_blocked`; service must not start Qlib training by default. |
| TRL | Governed DPO adapter and smoke baseline exist; production depends on runtime data gates such as sufficient FB-002 preference events and downstream consumer readiness. | Capability may be listed as `activation_blocked`; production DPO must stay outside this service boundary. |
| FinRL | Version pin and container exist; governed adapter/smoke path still blocked by RL path approval. | Reject production requests unless an explicit future task opens the RL path. |
| RLlib / Ray Tune | Version pins and deferred-prep scaffolds exist; RL path approval remains unmet. | Reject production requests; do not create a hidden training lane. |
| W&B | Experiment backend selector remains MLflow-first/offline-gated; W&B SDK and production backend are not active. | Reject W&B-backed production tracking or artifact writes. |

Out-of-scope actions that should remain delegated:

| Concern | Owner / gate |
|---|---|
| Registry artifact admission, promotion, and deployment-stage truth | Registry / governance contracts, not this service. |
| Runtime binding, paper/canary/live dispatch, kill switch, safe mode | `runtime-manager` and governance/deployment services. |
| Qlib / TRL production training runs | Their activation criteria and future approved execution slices. |
| FinRL / RLlib / Ray Tune production training | RL path approval gate and future RL implementation tasks. |
| W&B production experiment backend | W&B activation criteria and future experiment-bridge work. |

---

## 5. Compose and Discovery Map

`SVC-COMPOSE` is the direct prerequisite. The current compose file already
contains sibling future-state service wrappers:

| Service | Container port | Health path | Pattern to inherit |
|---|---:|---|---|
| `consultation-svc` | 8096 | `/health` | `PORT`, service data dir, named volume, host port env var |
| `source-ingest` | 8097 | `/health` | same |
| `search-svc` | 8098 | `/health` | same |
| `training-session-svc` | 8099 | `/health` | same |

Because 8096-8099 are already used, this packet intentionally does not choose
a port. The parent owner should pick a non-conflicting port and expose a
matching host env var such as `POLICY_LEARNING_PORT`.

Recommended discovery once implemented:

| Consumer/env | Target | Rationale |
|---|---|---|
| `PANTHEON_POLICY_LEARNING_API_URL` | `http://policy-learning-svc:<port>` | Explicit BFF/operator discovery path for future policy-learning status and rejection surfaces. |
| `POLICY_LEARNING_DATA_DIR` | `/data/policy-learning` | Service-owned durable stub proposal/replay store. |
| `BFF.depends_on` | Optional, parent-owned | Only add if BFF actually consumes this service in the same slice. Avoid hidden activation dependencies. |
| `smoke-stack` env | Optional, parent-owned | Add only if compose smoke validates this service in the parent task. |

---

## 6. Verification Surface (parent-owned)

This sidecar does not run implementation tests because the parent service does
not exist yet. Suggested parent-owned tests:

| Layer | Suggested test | Why |
|---|---|---|
| HTTP health/capabilities | Assert `/health` and capabilities list return stable JSON with all production adapters disabled by default. | Acceptance: health and capability list. |
| Job lifecycle | Create a stub job, read it by ID, list it, and verify deterministic status/replay fields. | Acceptance: proposal/status and replayability. |
| Rejection path | Reject a job with an explicit reason and verify rejection persists and appears in status/replay. | Acceptance: rejection API. |
| Adapter guard | Requests for `qlib`, `trl`, `finrl`, `rllib`, `ray_tune`, or `wandb` production mode must return a rejection/blocked response by default. | Acceptance: disabled production adapters rejected. |
| Storage | Use a temp `POLICY_LEARNING_DATA_DIR` to verify file-backed records survive service object reload where local patterns support it. | Acceptance: env-driven storage. |
| Compose | Run `docker compose -f docker-compose.yml config`; if smoke is wired, run the smoke profile path required by the parent closeout. | Acceptance: Dockerfile and compose wiring. |

---

## 7. Dependency Map

### Direct prerequisite

| Dependency | Status | Why it matters |
|---|---|---|
| `SVC-COMPOSE` | `done` | Provides the single-VM compose stack pattern, healthcheck shape, named-volume convention, and smoke profile precedent inherited by this service wrapper. |

### Adjacent services

| Task / service | Relationship |
|---|---|
| `SVC-TRAINING-SESSION-SERVICE` / `training-session-svc` | Already owns human/operator training session lifecycle, preview, and replay semantics. `policy-learning-svc` should not duplicate trainer controls; it should hold future policy-learning job proposals and adapter-boundary decisions. |
| `SVC-SEARCH-SERVICE`, `SVC-CONSULTATION-SERVICE-ACTIVATION`, `SVC-SOURCE-INGEST-SERVICE` | Sibling future-state wrappers using the same compose materialization pattern. None should become hidden dependencies for policy-learning. |
| `SVC-RESEARCH-ORCHESTRATOR-SERVICE`, `SVC-RESEARCH-WORKER-GATEWAY` | Future research execution lanes. `policy-learning-svc` should not silently route production training work into them until an explicit activation task authorizes it. |

### Downstream consumers

| Consumer | Dependency on this boundary |
|---|---|
| Future BFF/operator policy-learning views | Need an explicit service URL and stable status/rejection read shape before showing policy-learning job state. |
| Future adapter activation tasks | Can consume the capability/rejection record as evidence that activation remains closed until gates clear. |
| Future health/observability unification | May need consistent `/health` or later `/readyz` shape once this service is wired. |

---

## 8. Reviewer Checklist for Copilot

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical, runtime, registry, governance, compose, and adapter edits? | Yes. Only this support packet was created. |
| Does it preserve the non-production boundary? | Yes. Every production adapter family is explicitly blocked by default and mapped to existing gates. |
| Does it avoid choosing final implementation details the parent owner should decide? | Yes. Route names are proposed, and port/BFF/smoke wiring are parent-owned decisions. |
| Is the dependency on `SVC-COMPOSE` represented accurately? | Yes. The packet maps the inherited Dockerfile/data-dir/healthcheck/named-volume pattern and notes occupied ports. |
| Does the test surface cover the parent acceptance criteria? | Yes. It covers health, capabilities, proposal/status, rejection, disabled adapter requests, storage, and compose validation. |

---

## 9. Handoff

**To**: `Copilot`
**From**: `Codex2`
**Requested review outcome**: Approve this sidecar if it is accurate as a
support packet for parent `SVC-POLICY-LEARNING-BOUNDARY`.

Recommended parent-owner use:

1. Use §2 as the parent acceptance checklist map.
2. Use §3 and §5 as implementation prompts, not as canonical API/port truth.
3. Keep §4 intact: this service boundary exists to make production learning
   activation explicit and rejectable, not to bypass Qlib, TRL, RL, or W&B
   gates.
4. Treat §6 as the minimum parent-owned test surface before the parent moves
   to review.
