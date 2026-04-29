# SVC-OSS-DORMANT-COMPOSE-PROFILES Acceptance Packet and Dependency Map

**Sidecar Task ID**: `SVC-OSS-DORMANT-COMPOSE-PROFILES-SIDECAR-ACCEPTANCE`  
**Parent Task**: `SVC-OSS-DORMANT-COMPOSE-PROFILES`  
**Parent Owner**: `Codex2`  
**Parent Reviewer**: `Claude`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `acceptance_packet`  
**Date**: 2026-04-29

> This is a support artifact only. It does not update canonical truth, L1
> policy, core contracts, runtime code, registry logic, or compose ownership.
> The parent owner decides whether and how to absorb this packet into the
> `SVC-OSS-DORMANT-COMPOSE-PROFILES` implementation and closeout.

---

## 1. Scope Snapshot

`SVC-OSS-DORMANT-COMPOSE-PROFILES` adds non-default dormant/offline compose
smoke packaging for OSS scaffolds that are already activation-gated. The parent
acceptance target is narrow:

| Parent acceptance item | Acceptance expectation |
|---|---|
| Dormant profiles are not in default compose startup | Profiled services must require an explicit compose profile or equivalent explicit smoke target. |
| Profile commands require explicit deferred-prep flags or env vars | Each smoke command must use the same explicit gate mechanism already reviewed by the prerequisite closeout task. |
| No ports, secrets, or live broker routes are exposed | Dormant smoke services must not publish ports, mount secrets, define live broker URLs, or run long-lived runtime/registry services. |
| Compose config and targeted smoke commands are documented and testable | `docker compose config` must parse, and each dormant target must have an explicit command the reviewer can run. |

Current task state read from `ai-status.json`:

| Task | Owner | Reviewer | Status |
|---|---|---|---|
| `SVC-OSS-DORMANT-COMPOSE-PROFILES` | `Codex2` | `Claude` | `review_approved` |
| `SVC-OSS-DORMANT-COMPOSE-PROFILES-SIDECAR-ACCEPTANCE` | `Codex` | `Claude` | `review_approved` |

---

## 2. Dependency Map

All direct prerequisites listed by the parent task are already `done` in
`ai-task-archive/tasks/`.

| Dependency | Terminal evidence to preserve in parent acceptance |
|---|---|
| `SVC-FINRL-DORMANT-SCAFFOLD-CLOSEOUT` | FinRL smoke and worker fail closed without explicit prep gates; gated smoke emits `artifact_state=draft`, `deployment_stage=none`, and closed governance gate; no registry/governance/paper/live write path. |
| `SVC-RLLIB-RAYTUNE-DORMANT-SCAFFOLD-CLOSEOUT` | RLlib and Ray Tune workers require explicit env gates; smokes require `--enable-deferred-prep`; outputs stay draft/none/non-writing; 29 targeted tests were reviewed. |
| `SVC-WANDB-OFFLINE-PREP-SCAFFOLD-CLOSEOUT` | W&B prep scaffold remains fail-closed behind `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`; only offline/dryrun modes; no W&B SDK import/pin/networked activation; canonical `artifact_state`/`deployment_stage` rules enforced before backend side effects. |
| `SVC-QLIB-GATED-PREACTIVATION-PREFLIGHT` | Qlib pre-activation preflight is offline and fail-closed for missing RS-003 candidate, governed dataset, or StrategySpec binding; smoke and 19 unit tests were reviewed. |
| `SVC-TRL-GATED-PREACTIVATION-PREFLIGHT` | TRL preflight is non-writing and fail-closed for FB-002/preference-pair readiness; TRL smoke remains stub/draft/none; 29 targeted tests were reviewed. |

Implication for this parent task: compose packaging should only make the existing
reviewed smoke paths runnable as explicit dormant jobs. It should not reopen any
activation gate, write-owner boundary, upstream SDK activation, registry
promotion path, or paper/live route.

---

## 3. Candidate Compose Inventory

Current parent worktree read-only snapshot shows a candidate `dormant-smoke`
profile with these services:

| Compose service | Intended scaffold | Candidate command | Gate evidence to preserve |
|---|---|---|---|
| `finrl-dormant-smoke` | FinRL | `python smoke_test.py --enable-deferred-prep --backend stub` | Uses the reviewed explicit CLI gate; expected output remains draft/none with closed gate. |
| `rllib-dormant-smoke` | RLlib | `python smoke_test.py --enable-deferred-prep --backend stub` | Uses the reviewed explicit CLI gate; expected output remains draft/none with closed gate. |
| `ray-tune-dormant-smoke` | Ray Tune | `python ray_tune_smoke_test.py --enable-deferred-prep --backend stub` | Uses the reviewed explicit CLI gate; expected output remains draft/none with closed gate. |
| `qlib-dormant-smoke` | Qlib | `python smoke_test.py --backend stub` | Runs the reviewed offline stub smoke. Parent should keep production Qlib activation gated by preflight, not by this smoke. |
| `trl-dormant-smoke` | TRL | `python services/learning/trl/smoke_test.py --backend stub` | Runs the reviewed stub smoke; output remains draft/none. |
| `experiments-dormant-smoke` | Registry experiments / W&B prep lane | `python smoke_test.py --backend memory` | Uses the memory backend for packaging smoke. This does not activate SDK-backed W&B. |

Expected service-level packaging constraints:

| Constraint | Required parent condition |
|---|---|
| Non-default startup | Each dormant smoke service has `profiles: ["dormant-smoke"]`; no dormant smoke service should appear in default `docker compose config --services`. |
| Short-lived execution | Each service exits after its smoke command and uses `restart: "no"`. |
| No exposed runtime surface | Dormant smoke services define no `ports`, no `secrets`, no live broker env vars, and no service health dependency that starts runtime stacks as an implicit side effect. |
| Inert image default | Each relevant Dockerfile should have an inert default `CMD`; compose commands opt into smoke explicitly. |

---

## 4. Acceptance Risks Found During Sidecar Read

This section is intentionally a support note, not a parent code change.

| Risk | Why it matters | Parent-owner action before parent review |
|---|---|---|
| Build context mismatch for FinRL, RLlib/Ray Tune, and Qlib candidate services | Current compose snapshot uses `build.context: .` with Dockerfiles that still contain service-local sources such as `COPY requirements.txt`, `COPY adapter`, `COPY config.py`, and `COPY examples`. With repo-root context and no root `requirements.txt`, those builds are likely to fail even though `docker compose config` parses. | Align build contexts with each Dockerfile's source assumptions, or rewrite the Dockerfile `COPY` sources to repo-root paths. Then run at least one build or smoke for each affected service. |
| `experiments-dormant-smoke` uses memory backend only | This is acceptable for no-network packaging smoke, but it does not exercise the W&B offline backend path that the prerequisite closeout reviewed. | If parent wants W&B-specific packaging evidence, add a separate explicit offline-only W&B smoke command gated by `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`; otherwise document memory smoke as packaging-only. |
| `PANTHEON_DEFERRED_PREP_GATE=1` is generic | The prerequisite gates are service-specific for workers and/or CLI flags. A generic env var is harmless only if it is documentary and not treated as activation authority. | Confirm no dormant service relies on the generic env var as a substitute for reviewed flags such as `--enable-deferred-prep` or W&B's `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`. |

Closeout note: Claude approved this sidecar packet on 2026-04-29. The sidecar
identified the build-context risk accurately; parent owner Codex2 resolved it
in the parent implementation before parent review. This sidecar remains a
support-only record and did not change canonical truth, L1 policy, runtime
code, registry code, or compose ownership.

---

## 5. Reviewer Acceptance Checklist for Parent Closeout

| Check | Expected answer before `SVC-OSS-DORMANT-COMPOSE-PROFILES` enters review |
|---|---|
| Do default compose services exclude dormant smoke jobs? | Yes. Default service list should omit `finrl-dormant-smoke`, `rllib-dormant-smoke`, `ray-tune-dormant-smoke`, `qlib-dormant-smoke`, `trl-dormant-smoke`, and `experiments-dormant-smoke`. |
| Does explicit profile expose only dormant jobs as opt-in additions? | Yes. `docker compose --profile dormant-smoke config --services` should include the six dormant services. |
| Are activation gates still closed? | Yes. FinRL/RLlib/Ray Tune use explicit CLI gates; Qlib/TRL remain stub/offline and do not bypass preflight approval; experiments memory smoke does not activate SDK-backed W&B. |
| Are all Dockerfile defaults inert? | Yes. Image defaults print an activation-closed message or otherwise do not run workers, registry writes, live routes, or paper/canary/live flows. |
| Can each service build with the configured compose context? | Yes. This must be proven after resolving the current context mismatch risk for FinRL/RLlib/Qlib. |
| Are ports/secrets/live broker routes absent? | Yes. Dormant smoke services should have no `ports`, no `secrets`, no broker credentials, and no live/paper/canary env wiring. |
| Is verification documented and rerunnable? | Yes. Parent closeout should record the exact `docker compose config`, `docker compose build`, and targeted `docker compose --profile dormant-smoke run --rm ...` commands that passed. |

---

## 6. Suggested Verification Commands

Config shape:

```bash
docker compose config >/tmp/pantheon-compose-config.txt
docker compose --profile dormant-smoke config >/tmp/pantheon-compose-dormant-config.txt
docker compose config --services
docker compose --profile dormant-smoke config --services
```

Default-profile exclusion check:

```bash
docker compose config --services | rg 'dormant-smoke|finrl-dormant|rllib-dormant|ray-tune-dormant|qlib-dormant|trl-dormant|experiments-dormant' && exit 1 || true
```

Explicit smoke targets after build-context alignment:

```bash
docker compose --profile dormant-smoke build finrl-dormant-smoke rllib-dormant-smoke ray-tune-dormant-smoke qlib-dormant-smoke trl-dormant-smoke experiments-dormant-smoke
docker compose --profile dormant-smoke run --rm finrl-dormant-smoke
docker compose --profile dormant-smoke run --rm rllib-dormant-smoke
docker compose --profile dormant-smoke run --rm ray-tune-dormant-smoke
docker compose --profile dormant-smoke run --rm qlib-dormant-smoke
docker compose --profile dormant-smoke run --rm trl-dormant-smoke
docker compose --profile dormant-smoke run --rm experiments-dormant-smoke
```

Optional source-level guardrails:

```bash
rg -n 'ports:|secrets:|BROKER|LIVE|CANARY|PAPER' docker-compose.yml
rg -n 'wandb|mlflow' services/registry/experiments/requirements.txt services/registry/experiments/*.py
```

---

## 7. Sidecar Verification

Focused checks run by this sidecar:

```bash
docker compose config >/tmp/pantheon-compose-config.txt
docker compose --profile dormant-smoke config >/tmp/pantheon-compose-dormant-config.txt
docker compose config --services
docker compose --profile dormant-smoke config --services
```

Observed result:

- Compose syntax currently parses for default and explicit `dormant-smoke`
  profile.
- Default `docker compose config --services` output omits all six candidate
  dormant smoke services.
- Explicit `docker compose --profile dormant-smoke config --services` output
  includes all six candidate dormant smoke services.
- Build/run evidence was not produced by this sidecar because parent
  implementation is still in progress and the build-context risk above belongs
  to parent implementation, not to this support packet.

---

## 8. Handoff to Claude

**To**: `Claude`  
**From**: `Codex`  
**Requested review outcome**: Approve this sidecar if it is accurate as a
support-only acceptance packet for parent
`SVC-OSS-DORMANT-COMPOSE-PROFILES`.

Recommended parent-owner use:

1. Use Sections 2, 5, and 6 as the parent closeout checklist.
2. Resolve the build-context mismatch before asking Codex to review the parent
   task.
3. Keep the parent implementation scoped to dormant packaging. Do not use this
   packet to change canonical truth, reopen activation gates, or introduce live
   runtime/registry routes.
