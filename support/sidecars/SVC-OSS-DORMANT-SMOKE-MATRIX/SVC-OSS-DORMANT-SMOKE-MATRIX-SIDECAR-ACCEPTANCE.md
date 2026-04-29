# SVC-OSS-DORMANT-SMOKE-MATRIX Acceptance Packet and Dependency Map

**Sidecar task:** `SVC-OSS-DORMANT-SMOKE-MATRIX-SIDECAR-ACCEPTANCE`
**Parent task:** `SVC-OSS-DORMANT-SMOKE-MATRIX`
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude`
**Helper kind:** `acceptance_packet`
**Date:** 2026-04-29

> This is a support artifact only. It does not update canonical truth, L1 policy,
> core contracts, runtime code, registry logic, governance behavior, or compose
> ownership. The parent owner decides whether and how to absorb this packet into
> the `SVC-OSS-DORMANT-SMOKE-MATRIX` implementation and closeout.

---

## 1. Scope Snapshot

`SVC-OSS-DORMANT-SMOKE-MATRIX` should add an activation-gated dormant OSS smoke
matrix for the already-scaffolded OpenClaw, Qlib, TRL, FinRL, RLlib, Ray Tune,
and W&B offline/deferred-prep rows.

The parent acceptance target from task state is:

| Parent acceptance item | Acceptance expectation |
|---|---|
| Smoke matrix records `activated=false` for every gated row | Every row must show activation remains closed, blocked, deferred, or facade-only; no row may imply production activation. |
| All commands are offline or local fixture only | Stub/offline commands should be default; optional upstream dependency checks must be clearly separate from activation permission. |
| No registry, governance network, or live execution writes occur | Rows may produce local registry-shaped artifacts, metadata, or output refs only; they must not promote artifacts, change governance state, start LEAN/live paths, or open broker sessions. |
| Failures distinguish missing optional deps from activation permission | Missing upstream packages/images are dependency failures; blocked gates, denied dispatch, or explicit missing prep flags are activation-permission failures. |

Task state at owner closeout start, read from `ai-status.json`:

| Task | Owner | Reviewer | Status |
|---|---|---|---|
| `SVC-OSS-DORMANT-SMOKE-MATRIX` | `Claude` | `Codex` | `in_progress` |
| `SVC-OSS-DORMANT-SMOKE-MATRIX-SIDECAR-ACCEPTANCE` | `Codex` | `Claude` | `review_approved`; owner closeout pending |

---

## 2. Dependency Map

All direct prerequisites named by the parent task are `done` in
`ai-status.json` / `ai-task-archive/tasks/`.

| Dependency | Status | Parent smoke-matrix evidence to preserve |
|---|---:|---|
| `SVC-OPENCLAW-RUNTIME-ADOPTION-SCAFFOLD` | done | OpenClaw adapter exposes capability metadata only: `activation_state=facade_only`, broker/paper/live/capital paths are `deferred`, session creation returns `503` with `CAPABILITY_DENIED`, and research-worker-gateway rejects OpenClaw dispatch as `production_adapter_disabled`. |
| `SVC-QLIB-GATED-PREACTIVATION-PREFLIGHT` | done | Qlib stub smoke emits `artifact_state=draft` and `deployment_stage=none`; preflight with missing RS-003 candidate, governed dataset, or StrategySpec binding returns `activation_allowed=false`. |
| `SVC-TRL-GATED-PREACTIVATION-PREFLIGHT` | done | TRL stub smoke emits `artifact_state=draft` and `deployment_stage=none`; preflight blocks production activation when FB-002 volume and preference-pair gates are not met. |
| `SVC-FINRL-DORMANT-SCAFFOLD-CLOSEOUT` | done | FinRL smoke requires `--enable-deferred-prep`; with the flag it emits draft/none output plus `gate_state=closed`; without the flag it exits before workflow execution. |
| `SVC-RLLIB-RAYTUNE-DORMANT-SCAFFOLD-CLOSEOUT` | done | RLlib and Ray Tune smokes require `--enable-deferred-prep`; with the flag they emit draft/none output plus `gate_state=closed`; without the flag they exit before workflow execution. |
| `SVC-WANDB-OFFLINE-PREP-SCAFFOLD-CLOSEOUT` | done | W&B is prep-only and offline/dryrun only. `EXPERIMENT_BACKEND=wandb` must require `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`; `PANTHEON_WANDB_MODE=online` must fail closed; no W&B SDK import or requirements pin should appear. |
| `SVC-OSS-DORMANT-COMPOSE-PROFILES` | done | Non-default `dormant-smoke` profile exists for FinRL, RLlib, Ray Tune, Qlib, TRL, and experiments packaging. Default compose excludes these services; explicit profile includes them; no ports, secrets, or live broker routes are exposed. |

Implication for the parent task: this matrix should summarize and re-run the
existing reviewed dormant/offline evidence. It should not reopen activation
gates, add real upstream SDK activation, create registry/governance writes, or
change default compose startup.

---

## 3. Recommended Matrix Contract

The parent implementation can be code, script output, markdown evidence, or a
small JSON artifact, but each row should answer the same questions.

| Field | Required meaning |
|---|---|
| `framework` | One of `openclaw`, `qlib`, `trl`, `finrl`, `rllib`, `ray_tune`, `wandb`. |
| `command` | Exact offline/stub/local-fixture command used for the row. |
| `activated` | Must be `false` for every row in this task. |
| `activation_state` | One of `facade_only`, `deferred`, `blocked`, `closed`, `fail_closed`, or equivalent. |
| `artifact_state` | `draft` for artifact-producing rows; `not_applicable` for capability-only rows. |
| `deployment_stage` | `none` for artifact-producing rows; `not_applicable` for capability-only rows. |
| `offline_only` | Must be true unless the row is a pure local capability/test surface that performs no network write. |
| `registry_write` | Must be false. Local registry-shaped metadata is acceptable only as smoke output. |
| `governance_write` | Must be false. |
| `live_execution` | Must be false. |
| `failure_classification` | Distinguish `activation_denied` from `optional_dependency_missing` and normal `passed_closed`. |

Rows should not conflate "the stub smoke passed" with "production activation is
allowed." For Qlib and TRL, the matrix should include both a passing stub smoke
and a fail-closed preflight result. For FinRL, RLlib, and Ray Tune, the matrix
should include a negative no-flag check or equivalent evidence that the explicit
deferred-prep gate is still required.

---

## 4. Candidate Matrix Rows

| Row | Offline / local command | Required closed-state evidence |
|---|---|---|
| OpenClaw | `python3 -m pytest services/openclaw-gateway-adapter/ services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py -q` | Capabilities stay `facade_only` / `deferred`; session creation and worker dispatch are denied; research-worker-gateway reports OpenClaw `gate_state=fail_closed` and `allowed_scope=capability_metadata_read_only`. |
| Qlib smoke | `python3 services/research/qlib/smoke_test.py --backend stub` | Output includes `artifact_state: draft`, `deployment_stage: none`, and `assertions: OK`. |
| Qlib preflight | `printf '{}' \| python3 services/research/qlib/preflight.py` | Exit code `2`; JSON includes `activation_allowed=false` and blocked `rs003_candidate`, `governed_dataset`, and `strategy_spec_binding` gates. |
| TRL smoke | `python3 services/learning/trl/smoke_test.py --backend stub` | Output includes `artifact_state: draft`, `deployment_stage: none`, governed storage under `learning/trl/`, and `assertions: OK`. |
| TRL preflight | `python3 -c 'from services.learning.trl.preflight import run_trl_preflight; import json; print(json.dumps(run_trl_preflight([], []).to_dict(), sort_keys=True))'` | JSON includes `activation_allowed=false`, required gates `fb002_volume` and `preference_pairs` blocked, informational gates unknown/blocked only as evidence. |
| FinRL smoke | `python3 services/research/finrl/smoke_test.py --enable-deferred-prep --backend stub` | Output includes `artifact_state: draft`, `deployment_stage: none`, `candidate_next_state: candidate`, `gate_state: closed`, and `assertions: OK`. |
| FinRL no-flag guard | `python3 services/research/finrl/smoke_test.py --backend stub` | Exit code `2`; message says deferred prep is disabled by default and `--enable-deferred-prep` is required. |
| RLlib smoke | `python3 services/research/rllib/smoke_test.py --enable-deferred-prep --backend stub` | Output includes `artifact_state: draft`, `deployment_stage: none`, `candidate_next_state: candidate`, `gate_state: closed`, and `assertions: OK`. |
| RLlib no-flag guard | `python3 services/research/rllib/smoke_test.py --backend stub` | Exit code `2`; message says deferred prep is disabled by default and `--enable-deferred-prep` is required. |
| Ray Tune smoke | `python3 services/research/rllib/ray_tune_smoke_test.py --enable-deferred-prep --backend stub` | Output includes `artifact_type: optimizer_result`, `artifact_state: draft`, `deployment_stage: none`, `candidate_next_state: candidate`, `gate_state: closed`, and `assertions: OK`. |
| Ray Tune no-flag guard | `python3 services/research/rllib/ray_tune_smoke_test.py --backend stub` | Exit code `2`; message says deferred prep is disabled by default and `--enable-deferred-prep` is required. |
| W&B offline prep | `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 PANTHEON_WANDB_MODE=offline EXPERIMENT_BACKEND=wandb python3 services/registry/experiments/smoke_test.py --backend wandb` | Output says backend `wandb` passed; adapter tests cover `pantheon.wandb.prep_only=true`, canonical `artifact_state` / `deployment_stage`, and offline metadata shape. |
| W&B config gate | `(cd services/registry/experiments && EXPERIMENT_BACKEND=wandb python3 -c 'import config')` | Exit code `1`; error says W&B is reserved for deferred prep and requires `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`. |
| W&B online-mode guard | `(cd services/registry/experiments && PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 EXPERIMENT_BACKEND=wandb PANTHEON_WANDB_MODE=online python3 -c 'import config')` | Exit code `1`; error says only `offline` and `dryrun` modes are supported. |
| Compose profile shape | `docker compose config --quiet && docker compose --profile dormant-smoke config --quiet` | Compose parses in default and explicit dormant profile. |
| Compose default exclusion | `docker compose config --services \| rg 'dormant-smoke|finrl-dormant|rllib-dormant|ray-tune-dormant|qlib-dormant|trl-dormant|experiments-dormant'` | No matches in default service list; the command exits `1` from `rg`, which is the expected exclusion signal. |
| Compose explicit inclusion | `docker compose --profile dormant-smoke config --services \| rg 'dormant-smoke|finrl-dormant|rllib-dormant|ray-tune-dormant|qlib-dormant|trl-dormant|experiments-dormant'` | Matches all six dormant compose smoke services. |

---

## 5. Acceptance Risks For Parent Owner

These are support findings for parent review, not sidecar code changes.

| Risk | Why it matters | Parent-owner action before review |
|---|---|---|
| W&B direct smoke currently bypasses the config selector gate | `python3 services/registry/experiments/smoke_test.py --backend wandb` passes even without `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1` because it instantiates `OfflineWandbPrepBackend()` directly. The config selector is fail-closed, but the direct smoke command by itself is not proof of activation permission. | Matrix should either invoke W&B through the explicit env-gated path and record the config-gate negative checks, or tighten the direct smoke wrapper so `--backend wandb` requires the deferred-prep flag. |
| Compose `experiments-dormant-smoke` uses `--backend memory` | Memory backend is acceptable for packaging smoke, but it is not W&B offline-prep evidence. | Parent matrix should include a distinct W&B offline row with `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`, or explicitly label compose experiments smoke as packaging-only. |
| Qlib and TRL stub smokes are not activation checks | Passing stub smokes prove artifact-shape and non-writing behavior, not production readiness. | Include Qlib and TRL preflight rows with `activation_allowed=false` alongside their passing stub smokes. |
| Optional upstream dependency failures can be misread as gate failures | `--backend qlib`, `--backend trl`, `--backend finrl`, `--backend rllib`, or `--backend tune` may fail because optional upstream packages are missing. That is separate from activation permission. | Keep upstream-import checks optional and classify failures as `optional_dependency_missing` unless the closed gate/preflight logic is what failed. |
| OpenClaw upstream availability is not required for closed-state proof | If the upstream OpenClaw gateway is absent, readiness may be degraded by design. | Accept `livez`/capability metadata and session/dispatch denial evidence as closed-state proof; do not require a live broker or upstream session. |

---

## 6. Reviewer Checklist For Parent Closeout

| Check | Pass condition |
|---|---|
| Matrix rows cover all seven target frameworks | OpenClaw, Qlib, TRL, FinRL, RLlib, Ray Tune, and W&B all appear with exact commands and observed closed-state fields. |
| Every row records `activated=false` | No row claims production, paper, canary, live, broker, or SDK-backed activation. |
| Artifact rows remain draft/none | Qlib, TRL, FinRL, RLlib, and Ray Tune show `artifact_state=draft` and `deployment_stage=none`; W&B mirrors canonical state metadata without activating SDK-backed online behavior. |
| Gate rows remain closed | OpenClaw is facade/deferred; Qlib and TRL preflights are `activation_allowed=false`; FinRL/RLlib/Ray Tune require explicit deferred-prep flags; W&B requires the deferred-prep env and offline/dryrun mode. |
| Default compose remains unchanged | Default `docker compose config --services` excludes dormant services; explicit `--profile dormant-smoke` includes them. |
| No live or write routes are opened | No ports/secrets/live broker routes in dormant profile; no registry promotions, governance writes, LEAN/live execution paths, or OpenClaw session creation. |
| Failure taxonomy is explicit | Optional dependency/import failures are separated from permission/gate failures. |
| Evidence is rerunnable | Parent closeout records exact commands, exit codes for expected failures, and key output fields. |

---

## 7. Sidecar Verification Performed

Focused checks run by this sidecar on 2026-04-29:

```bash
python3 services/research/finrl/smoke_test.py --enable-deferred-prep --backend stub
python3 services/research/rllib/smoke_test.py --enable-deferred-prep --backend stub
python3 services/research/rllib/ray_tune_smoke_test.py --enable-deferred-prep --backend stub
python3 services/research/qlib/smoke_test.py --backend stub
python3 services/learning/trl/smoke_test.py --backend stub
PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 PANTHEON_WANDB_MODE=offline EXPERIMENT_BACKEND=wandb python3 services/registry/experiments/smoke_test.py --backend wandb
python3 services/research/finrl/smoke_test.py --backend stub
python3 services/research/rllib/smoke_test.py --backend stub
python3 services/research/rllib/ray_tune_smoke_test.py --backend stub
printf '{}' | python3 services/research/qlib/preflight.py
python3 -c 'from services.learning.trl.preflight import run_trl_preflight; import json; print(json.dumps(run_trl_preflight([], []).to_dict(), sort_keys=True))'
EXPERIMENT_BACKEND=wandb python3 -c 'import config'                    # from services/registry/experiments
PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 EXPERIMENT_BACKEND=wandb PANTHEON_WANDB_MODE=online python3 -c 'import config'  # from services/registry/experiments
python3 -m pytest services/openclaw-gateway-adapter/ services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py -q
python3 -m unittest discover -s services/research/finrl -p 'test_*.py'
python3 -m unittest discover -s services/research/rllib -p 'test_*.py'
python3 -m unittest discover -s services/research/qlib -p 'test_*.py'
python3 -m unittest discover -s services/learning/trl -p 'test_*.py'
python3 -m unittest test_adapter.py                                    # from services/registry/experiments
docker compose config --quiet
docker compose --profile dormant-smoke config --quiet
docker compose config --services | rg 'dormant-smoke|finrl-dormant|rllib-dormant|ray-tune-dormant|qlib-dormant|trl-dormant|experiments-dormant'
docker compose --profile dormant-smoke config --services | rg 'dormant-smoke|finrl-dormant|rllib-dormant|ray-tune-dormant|qlib-dormant|trl-dormant|experiments-dormant'
```

Observed results:

- FinRL, RLlib, Ray Tune, Qlib, TRL, and W&B offline smoke commands passed.
- FinRL, RLlib, and Ray Tune no-flag commands exited `2` with deferred-prep
  gate messages.
- Qlib preflight exited `2` with `activation_allowed=false`.
- TRL preflight emitted `activation_allowed=false`.
- W&B config import failed closed without `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`
  and failed closed for `PANTHEON_WANDB_MODE=online`.
- OpenClaw adapter and research-worker-gateway rejection tests passed:
  `30 passed in 1.87s`.
- FinRL unit tests passed: `14 tests`.
- RLlib/Ray Tune unit tests passed: `29 tests`.
- Qlib unit tests passed: `19 tests`.
- TRL unit tests passed: `29 tests`.
- Experiments adapter unit tests passed: `11 tests`.
- Compose default and dormant profile configs parsed.
- Default compose service list excluded dormant services; explicit
  `dormant-smoke` profile listed:
  `trl-dormant-smoke`, `finrl-dormant-smoke`, `ray-tune-dormant-smoke`,
  `experiments-dormant-smoke`, `qlib-dormant-smoke`, and
  `rllib-dormant-smoke`.

---

## 8. Handoff To Claude

**To:** `Claude`
**From:** `Codex`
**Requested review outcome:** Approve this sidecar if it is accurate as a
support-only acceptance packet for parent `SVC-OSS-DORMANT-SMOKE-MATRIX`.

Recommended parent-owner use:

1. Use Sections 3, 4, and 6 as the parent smoke-matrix contract and review gate.
2. Decide whether to tighten the direct W&B smoke wrapper or record config-gate
   negative checks beside the W&B offline smoke row.
3. Keep parent implementation scoped to activation-gated smoke evidence. Do not
   use this packet to change canonical truth, reopen production activation, or
   introduce runtime/registry/governance write routes.

---

## 9. Owner Closeout Note

Claude approved this sidecar packet on 2026-04-29 with no requested changes:
all seven framework rows are covered, the W&B direct-smoke bypass and compose
memory-backend limitations are flagged for the parent owner, and no canonical
truth changes are included.

Closeout verification was limited to this support artifact and git staging
scope. The closeout commit must stage only:

```text
support/sidecars/SVC-OSS-DORMANT-SMOKE-MATRIX/SVC-OSS-DORMANT-SMOKE-MATRIX-SIDECAR-ACCEPTANCE.md
```
