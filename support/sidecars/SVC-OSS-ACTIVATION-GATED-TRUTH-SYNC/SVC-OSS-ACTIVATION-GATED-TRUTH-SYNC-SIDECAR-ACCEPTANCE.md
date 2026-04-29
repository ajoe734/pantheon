# SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC Sidecar Acceptance Packet

**Task ID**: `SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE`
**Parent Task**: `SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC`
**Helper Kind**: `acceptance_packet`
**Owner**: Codex
**Reviewer**: Claude
**Prepared**: 2026-04-29
**Scope**: support-only sidecar; this packet does not mutate canonical truth.

## Execution Boundary

This sidecar is limited to acceptance support for the parent truth-sync task. It may be used by the
parent owner to update or reject updates in canonical docs, but it does not itself approve runtime
activation or alter service behavior.

The support packet was prepared from:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/svc_oss_activation_gated_truth_sync_sidecar_acceptance.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`
- task archives for the nine completed dependencies
- targeted reads of the parent truth targets listed in the task:
  `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`,
  `RESEARCH_BACKEND_MATURITY_MATRIX.md`,
  `OSS_INTEGRATION_CHECKLIST.md`,
  `services/registry/experiments/WANDB_ACTIVATION.md`, and
  `OPENCLAW_RUNTIME_CONTRACT.md`

`current-work.md` and the full `ai-activity-log.jsonl` were not read for this sidecar.

## Dependency Map

All parent dependencies are closed as `done` in task archive state. Parent truth sync should treat
their delivered behavior as landed scaffold or prep evidence, not as production activation evidence.

| Dependency | Delivered support truth | Commit / verification anchor | Parent truth impact |
|---|---|---|---|
| `SVC-OPENCLAW-RUNTIME-ADOPTION-SCAFFOLD` | Fail-closed OpenClaw adapter capability metadata and research-worker rejection surface; broker sessions, paper/canary/live routes, and capital binding remain denied. | `941108d` (`941108d27cc2360c6c4d220aaa4ce9085908976d`); 28 pytest checks, py_compile, diff check. | Docs may say adapter/runtime-adoption scaffold exists, but must not say OpenClaw is an execution kernel or live-capital path. |
| `SVC-QLIB-GATED-PREACTIVATION-PREFLIGHT` | Qlib preflight rejects missing RS-003 candidate refs, insufficient governed dataset, and missing StrategySpec binding; report is offline/non-writing. | `6b44ab3` (`6b44ab39229afa009d55c7e0645253324d353ac3`); 19 Qlib unit tests and smoke test. | Qlib has a runnable smoke/preflight baseline; production activation remains blocked on RS-003, dataset, and StrategySpec gates. |
| `SVC-TRL-GATED-PREACTIVATION-PREFLIGHT` | TRL preflight rejects insufficient FB-002 volume and preference pairs; LP-002/downstream readiness remain runtime gates; no canonical writes. | `49488cd` (`49488cd9fc13655c79e0fe664e703f9c2d4355a3`); 29 unittest checks and TRL smoke. | TRL has prep-only runnable checks; production DPO activation remains blocked on runtime feedback volume, approved LP-002, and downstream consumer readiness. |
| `SVC-FINRL-DORMANT-SCAFFOLD-CLOSEOUT` | FinRL worker/smoke require explicit deferred-prep gate; outputs remain `artifact_state=draft`, `deployment_stage=none`, and non-writing. | `dba3e45` (`dba3e455bbb913ea571b701a3ba4031016f76fa6`); fail-closed smoke/worker gates and draft/none envelope verified. | FinRL docs should no longer imply missing dormant scaffold; RL production activation remains closed behind RL path approval. |
| `SVC-RLLIB-RAYTUNE-DORMANT-SCAFFOLD-CLOSEOUT` | RLlib and Ray Tune workers/smokes require explicit env/CLI gates; outputs remain draft/none and non-writing. | `65f814e` (`65f814efbf399b3e386fc5a4926be617ad5d410a`); 29 rllib/ray_tune tests and smoke assertions. | RLlib/Ray Tune docs should reflect dormant scaffold exists; governed train/eval, registry writes, and paper/live execution remain closed. |
| `SVC-WANDB-OFFLINE-PREP-SCAFFOLD-CLOSEOUT` | `EXPERIMENT_BACKEND=wandb` is available only behind `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`; W&B mode is offline/dryrun only; no SDK import or network activation. | `45d0ea3` (`45d0ea3e6a594d39fe6815022b0a7c433987d32c`); adapter tests, memory/wandb smoke, py_compile, SDK/requirements scrub. | W&B docs may say prep-only offline scaffold exists, but SDK-backed or online W&B remains deferred until all re-entry criteria pass. |
| `SVC-RESEARCH-GATEWAY-DORMANT-CAPABILITY-SURFACE` | Research-worker-gateway, research orchestrator, and policy-learning expose fail-closed read-only metadata for OpenClaw, Qlib, TRL, FinRL, RLlib, Ray Tune, and W&B. | `7c1835b` (`7c1835bdb2479da1c0c8661c45dc5c8f54e4cf10`); 16 HTTP/rejection-policy tests. | Parent docs can point to capability metadata surfaces, but must preserve `allowed_scope=capability_metadata_read_only`. |
| `SVC-OSS-DORMANT-COMPOSE-PROFILES` | Non-default dormant compose smoke services exist; profile commands require explicit deferred-prep flags; no ports, secrets, or live broker routes exposed. | `0a25cca` (`0a25ccac71b8c36b80ef03339b8d47c9846b69f8`); compose config checks, six-image dormant build, six offline smoke runs. | Parent docs can mention dormant smoke packaging, but default compose startup must remain non-activating. |
| `SVC-OSS-DORMANT-SMOKE-MATRIX` | Dormant OSS matrix reports all seven rows `gate_state=closed`, `activated=false`, and `matrix_passed=true`; commands are offline/local fixture only. | `628f851` (`628f8511a0a4c4a52d6517b251763b3f2d5aed99`); `python3 scripts/smoke_dormant_oss_matrix.py` exit 0 and reviewer reruns. | Parent docs can use the matrix as acceptance evidence that dormant scaffolds are present but not activated. |

## Parent Acceptance Checklist

The parent task should be accepted only if all of the following remain true after its canonical
truth edits:

1. **No missing-adapter claims for landed dormant work**
   - OpenClaw: fail-closed adapter/capability scaffold exists; broker/live/capital routes remain denied.
   - Qlib: governed adapter, preflight, and smoke-tested baseline exist.
   - TRL: governed adapter, preflight, and smoke-tested baseline exist.
   - FinRL: dormant worker/adapter/smoke path exists behind explicit deferred-prep gate.
   - RLlib/Ray Tune: dormant train/eval and search-output scaffolds exist behind explicit gates.
   - W&B: prep-only offline backend selector/scaffold exists; SDK-backed backend still does not.

2. **No production activation claims for gated rows**
   - OpenClaw remains a control-plane/runtime substrate, not an execution kernel.
   - Qlib remains blocked on RS-003 candidate, governed dataset proof, and target StrategySpec binding.
   - TRL remains blocked on FB-002 volume, preference-pair volume, approved LP-002, and downstream consumer readiness.
   - FinRL/RLlib/Ray Tune remain blocked on the RL path approval gate and Qlib approved-plus-stable evidence.
   - W&B remains blocked on all formal re-entry conditions for SDK-backed or networked activation.

3. **Development-allowed / activation-gated wording is preserved**
   - Dormant code, Dockerfiles, smoke tests, preflight reports, and read-only capability metadata may exist.
   - Production dispatch, registry/governance writes, paper/canary/live execution, broker routes, capital binding, and networked W&B remain closed unless a future gate explicitly opens them.

4. **Gate state remains explicit and dated where the current docs already date it**
   - W&B earliest eligible reopen remains 2026-05-15, subject to all six re-entry conditions.
   - RL re-entry remains after Qlib reaches `artifact_state=approved` and stays stable for 3 months.
   - Qlib, TRL, FinRL, RLlib/Ray Tune, and W&B owner lanes remain explicit.

5. **Evidence language is concrete**
   - Parent edits should cite the relevant completed task IDs or their evidence summaries instead of vague "future work" language.
   - Runtime or production readiness claims must name the remaining gate that authorizes activation.

## Targeted Sync Watchlist

These are not sidecar edits. They are the highest-value checks for the parent owner:

- `RESEARCH_BACKEND_MATURITY_MATRIX.md` currently has wording that says OpenClaw runtime adoption is still tracked separately / should be finished. After `SVC-OPENCLAW-RUNTIME-ADOPTION-SCAFFOLD`, that should be narrowed to fail-closed scaffold landed while production/broker/live/capital activation remains closed.
- `OSS_INTEGRATION_CHECKLIST.md` has TRL evidence wording that still mentions 16 unit tests from 2026-04-24, while `SVC-TRL-GATED-PREACTIVATION-PREFLIGHT` closed with 29 tests on 2026-04-29. If parent edits the checklist, update that evidence without changing activation status.
- W&B wording should distinguish between "prep-only backend selector/offline scaffold landed" and "SDK-backed or online backend still missing/deferred." Do not collapse those into a single "adapter missing" claim.
- Current-work sync is part of the parent task, but this sidecar did not read or edit `current-work.md`; parent owner should record the same "not enabled, development allowed" distinction there.

## Suggested Reviewer Questions

Claude should verify the parent packet against these questions before approving this sidecar or
absorbing it into the main truth sync:

1. Does every dormant OSS row with landed code have a doc statement that says the scaffold exists?
2. Does every gated row also have a doc statement that denies production activation until the named gate?
3. Are any words like "active", "production", "paper", "canary", "live", "enabled", "governed train/eval", or "networked backend" used without an explicit activation gate?
4. Does the parent update avoid changing L1 runtime policy beyond the intended OpenClaw fail-closed scaffold clarification?
5. Does the final parent handoff include exact verification commands and a task-scoped commit?

## Verification Performed

Focused sidecar verification was run from `/home/edna/code/pantheon`:

```bash
git diff --check -- support/sidecars/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.md
python3 scripts/smoke_dormant_oss_matrix.py --json-out /tmp/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.matrix.json
```

Results:

- Markdown diff check passed.
- Dormant OSS smoke matrix passed: 7 rows, 7 acceptable, `gate_state=closed` for 7/7 rows, `activated=false` for 7/7 rows.
- Evidence JSON was written to `/tmp/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.matrix.json`.

## Reviewer Verification (Claude — 2026-04-29)

Review was reassigned from Claude2 to Claude by the chair on 2026-04-29T22:07:37Z.

**Reviewer questions answered:**

1. Every dormant OSS row with landed code has an explicit statement that its scaffold exists (OpenClaw adapter, Qlib preflight, TRL preflight, FinRL worker, RLlib/Ray Tune workers, W&B offline selector). ✅
2. Every gated row also has a named, specific gate that must be cleared before production activation (RS-003, dataset, StrategySpec for Qlib; FB-002 volume and LP-002 for TRL; RL path approval for FinRL/RLlib/Ray Tune; six re-entry conditions for W&B). ✅
3. No production-enabling words appear without an explicit activation gate qualifier. All "paper/canary/live", "governed train/eval", and "networked backend" phrases are accompanied by "remains denied" or "remains blocked on" language. ✅
4. The sidecar itself does not change any L1 canonical policy file. `mutates_canonical: false` and the support boundary is clearly stated. ✅
5. The original packet includes exact verification commands with evidence (`python3 scripts/smoke_dormant_oss_matrix.py --json-out /tmp/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.matrix.json`) and all nine dependencies carry commit SHAs. ✅

**Reviewer rerun:**

```bash
python3 scripts/smoke_dormant_oss_matrix.py --json-out /tmp/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.matrix.json
```

Result: 7 rows, 7 acceptable, `gate_state=closed` for 7/7, `activated=false` for 7/7. All rows unchanged from Codex's original verification.

**Outcome:** Packet approved. All nine dependencies are correctly mapped. Acceptance checklist is accurate and complete. Sync watchlist items are correctly scoped to the parent owner. No canonical files were modified by this sidecar.

## Sidecar Acceptance

This sidecar is complete when:

- this support packet exists at the task artifact path;
- it maps all nine completed dependencies to parent acceptance implications;
- it preserves the support-only boundary and does not modify canonical truth;
- it is handed off to the assigned reviewer and possible absorption by the parent owner.

## Owner Finalization

Owner closeout was performed after Claude approval. No canonical truth files were edited by this
sidecar.

Finalization verification:

```bash
git add -N support/sidecars/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.md
git diff --check -- support/sidecars/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.md
python3 scripts/smoke_dormant_oss_matrix.py --json-out /tmp/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC-SIDECAR-ACCEPTANCE.finalize.matrix.json
```

Result: diff check passed; dormant OSS smoke matrix passed with 7 rows, 7 acceptable,
`gate_state=closed` for 7/7 rows, and `activated=false` for 7/7 rows.
