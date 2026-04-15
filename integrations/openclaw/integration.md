# OpenClaw Integration — Pin and Adapter Boundary

Last updated: 2026-04-15
Owner: BP5-OSS-001 (Codex)
Reviewer: Claude
Status: governed baseline pinned
Upstream repo: https://github.com/openclaw/openclaw
Canonical runtime contract: `OPENCLAW_RUNTIME_CONTRACT.md`

## 1. Locked Upstream Pin

| Field | Value |
|---|---|
| Repository | `https://github.com/openclaw/openclaw` |
| Selected stable tag | `v2026.4.7` |
| Selected commit | `5050017543011b61df67744ebc6368d889c25a95` |
| Release date | `2026-04-08` |
| npm package | `openclaw@2026.4.7` |
| Container image | `ghcr.io/openclaw/openclaw:2026.4.7` |
| Container digest | `sha256:be45b5187cbec1ff0f4e2503393d66acfc121c2d97eadf03bb1ac75826bad77c` |
| Website | `https://openclaw.ai` |
| Docs | `https://docs.openclaw.ai` |

## 2. Why This Pin Still Holds

This repo is intentionally **not** following the newest tag blindly.

As of `2026-04-15`, the upstream release state is:

- latest stable release: `v2026.4.14`, published `2026-04-14`
- latest prerelease: `v2026.4.15-beta.1`, published `2026-04-15`

We are keeping `v2026.4.7` for `BP5-OSS-001` because:

- `v2026.4.14` is newer but has not satisfied the repo's 48-hour soak policy yet
- `v2026.4.15-beta.1` is a prerelease and therefore not eligible for the governed baseline
- the task goal here is to lock one reproducible source + adapter seam, not to chase the moving latest release

Upgrade rule:

1. wait at least 48 hours after a stable upstream release is published
2. re-run `scripts/openclaw-smoke-test.sh`
3. update this file, `governance.md`, `evidence_pack.md`, and `OSS_INTEGRATION_CHECKLIST.md`
4. record the pin change in `ai-activity-log.jsonl`

## 3. Integration Mode

Pantheon integrates OpenClaw as an **external runtime dependency**, not as vendored source and not as a local rewrite.

Accepted mode:

- upstream GitHub source remains upstream-owned
- Pantheon may consume the published package / container artifacts
- Pantheon implements one governed boundary: `openclaw-gateway-adapter`

Rejected modes:

- vendoring or submodule-copying the OpenClaw source tree into Pantheon
- re-implementing OpenClaw runtime behavior inside LEAN or Pantheon services
- treating OpenClaw as the owner of registry, promotion, execution, or telemetry truth

## 4. Verified Upstream Runtime Surface

`BP5-OSS-001` only locks surfaces that were actually verified against the pinned upstream artifacts.

Verified upstream surface:

- the Git tag `v2026.4.7` resolves to commit `5050017543011b61df67744ebc6368d889c25a95`
- the GHCR image `ghcr.io/openclaw/openclaw:2026.4.7` is published and pullable
- the container can execute `openclaw --help`
- the container can execute `openclaw gateway --help`
- the upstream Docker docs define a gateway process with HTTP health endpoints `/healthz` and `/readyz` once a configured gateway is running

Not verified here, and therefore **not** part of the locked upstream promise:

- any upstream `/control/*` REST API
- any direct upstream notion of Pantheon `StrategySpec` or `WorkflowHandoff`
- any direct upstream write path into Pantheon registry, governance, telemetry, or LEAN

## 5. Governed Adapter Boundary

The governed seam is the Pantheon-side `openclaw-gateway-adapter`. The implementation home is reserved under `integrations/openclaw/adapter/`.

Adapter responsibilities:

| Responsibility | Direction | Notes |
|---|---|---|
| agent provisioning | Pantheon -> OpenClaw | map persona and capability snapshots into upstream agent/runtime concepts |
| session lifecycle | Pantheon -> OpenClaw | create, resume, terminate, and inspect runtime sessions |
| tool / skill mapping | Pantheon -> OpenClaw | filter through Pantheon RBAC before any upstream resolution |
| consultation routing | Pantheon -> OpenClaw | bridge consult requests and sub-agent orchestration |
| workflow handoff capture | OpenClaw -> Pantheon | capture raw upstream output for governed normalization |
| normalization | Pantheon | emit canonical `StrategySpec` + `WorkflowHandoff` |
| error governance | Pantheon | classify upstream failures into known / transport / unknown buckets |

Boundary invariants:

- adapter code lives on the Pantheon side only
- adapter may expose Pantheon-internal facade endpoints later, but those endpoints are **not** treated as native OpenClaw API
- adapter is the only allowed place to map OpenClaw runtime objects to Pantheon domain objects
- OpenClaw never receives authority over registry state, approval state, capital pools, runtime bindings, or LEAN deployment

## 6. Pantheon Adapter Facade

The following facade is a **future Pantheon internal surface**, derived from `OPENCLAW_RUNTIME_CONTRACT.md`. It is not claimed to be a native upstream OpenClaw endpoint set.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/control/personas/{persona_id}/sessions` | create a governed runtime session for one persona |
| `POST` | `/control/sessions/{session_id}/invoke` | invoke one governed session action |
| `POST` | `/control/consult/spawn` | spawn a governed consultation session |
| `GET` | `/control/sessions/{session_id}` | read Pantheon-side session state |
| `GET` | `/control/personas/{persona_id}/capabilities` | resolve Pantheon-filtered capabilities |
| `POST` | `/control/jobs` | schedule a Pantheon-governed workflow or cron job |
| `GET` | `/control/jobs/{job_id}` | read Pantheon job status |

## 7. Smoke Baseline for BP5-OSS-001

The smoke baseline is intentionally narrower than `BP5-OSS-002`.

`BP5-OSS-001` proves:

1. the selected Git tag and container artifact both exist
2. the pinned container exposes the expected CLI / gateway command surface
3. a raw upstream-style handoff fixture can be normalized into canonical Pantheon objects with the repo's current schemas

The executable entrypoint is:

```bash
bash scripts/openclaw-smoke-test.sh
```

Supporting fixture:

- `integrations/openclaw/fixtures/raw_research_handoff.minimal.json`

Normalization script:

- `services/control-plane/specs/normalize_handoff.py`

## 8. Out of Scope for BP5-OSS-001

The following are explicitly deferred to `BP5-OSS-002`:

- deciding the final transport between Pantheon and the OpenClaw gateway
- bootstrapping a configured OpenClaw gateway with Pantheon-specific auth/runtime settings
- invoking a real workflow through a live adapter path
- proving end-to-end job execution and raw output capture from a configured runtime

That separation is intentional: this task locks the upstream source and the governed seam first, so the next task can implement against a stable target instead of undocumented assumptions.
