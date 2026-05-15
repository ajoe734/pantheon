# Review: P2-RL-UPSTREAM-RUNTIME-SMOKE-001 (Claude, post-Codex2 reassignment)

- Task: `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` — FinRL RLlib Ray Tune governed runtime activation smoke
- Owner: Claude2
- Reviewer: Claude (auto-reassigned from Codex2 after repeated terminal quota failures)
- Review commit: `2be92ea`
- Decision: **Approved**

## Scope of this review

This review covers the second handoff from Claude2 (commit `2be92ea`) which
addresses the three findings raised in Codex2's prior review
(`support/reviews/P2-RL-UPSTREAM-RUNTIME-SMOKE-001-codex2-review.md`).

Codex2 was dispatch-paused on quota exhaustion at 2026-05-02T12:38:58Z, so the
review baton was reassigned to Claude. I confirmed the fixes against the
current worktree without re-running the activation smoke (no upstream FinRL/Ray
packages are installed in this environment, which is itself the expected
"explicit dependency error" outcome that the task brief permits).

## Verification I ran

- `git show --stat 2be92ea` — confirmed commit metadata and footer (`LLM-Agent: Claude2`, `Task-ID: P2-RL-UPSTREAM-RUNTIME-SMOKE-001`, `Reviewer: Codex2`).
- `python3 -m unittest discover -s services/research/finrl` → **16 OK**
- `python3 -m unittest discover -s services/research/rllib` → **33 OK**
- Manifest checksum verification of all 21 framework-prefixed evidence files in
  `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/manifest.json` against
  on-disk SHA-256 → **21 OK, 0 BAD**.
- Read of `activation_evidence_summary.json` — every acceptance gate is `true`:
  `all_gates_pass`, `bounded_governed_smoke_or_explicit_error`,
  `checksums_persisted`, `evaluator_packet_produced`, `no_broker_session`,
  `no_capital_binding`, `no_order_routing`,
  `no_paper_canary_live_promotion`, `reward_env_dataset_schema_enforced`.
- Inspection of each `*_real_backend_attempt.json` — all three record
  `status: dependency_or_config_error`, `silent_stub_fallback: false`, and the
  expected `ModuleNotFoundError` cause (`finrl` for FinRL; `ray` for RLlib and
  Ray Tune) with framework-prefixed traceback tails.
- Inspection of each `*_registry_entry.json` — all three carry
  `deployment_summary.current_stage = "none"` and `artifact_state = "draft"`.

## Codex2 finding closure

1. **Adapter schema/checksum support committed alongside evidence.**
   Commit `2be92ea` adds `services/research/finrl/adapter/finrl_adapter.py`,
   `services/research/finrl/adapter/__init__.py`,
   `services/research/rllib/adapter/rllib_adapter.py`,
   `services/research/rllib/adapter/ray_tune_adapter.py`, and the matching
   package `__init__.py`. The adapters expose
   `prepared_finrl_dataset_checksum`, `prepared_rllib_dataset_checksum`, and
   the dataset/environment schema fields that the per-framework artifact
   bundles persist. The previous "dirty worktree only" gap is closed.
2. **Per-framework manifest now includes evaluator_packet checksum.**
   `_persist_artifacts()` writes `<framework>_evaluator_packet.json` before
   computing the manifest checksum map; the duplicate top-level write in
   `main()` was removed. Manifest verification covers all 21 files.
3. **OSS_INTEGRATION_CHECKLIST lifecycle wording corrected.**
   FinRL/RLlib/Ray Tune rows now say "evidence produced for task
   P2-RL-UPSTREAM-RUNTIME-SMOKE-001" instead of "task ... closed", which is
   accurate while the task is still in review.

## Acceptance criteria

- **A1 — bounded governed smoke or explicit dependency error.** PASS. Each
  framework's `real_backend_attempt.json` records the upstream import failure
  with `silent_stub_fallback=false`; stub-backed handoff artifacts are still
  persisted as research-only output, which is the documented fallback path.
- **A2 — reward/env/dataset/artifact schemas with persisted checksums and
  evaluator packet.** PASS. Each `*_artifact_bundle.json` carries
  `dataset_checksum`, `dataset_schema`, and `environment_schema`; each
  `*_evaluator_packet.json` carries the EV-001 advisory packet; manifest covers
  all checksums.
- **A3 — research artifacts only.** PASS. All registry entries report
  `deployment_summary.current_stage = "none"` and `artifact_state = "draft"`,
  and the aggregate gate evidence sets `no_broker_session`, `no_order_routing`,
  `no_paper_canary_live_promotion`, and `no_capital_binding` to true.

## Notes (non-blocking, owner discretion)

- `services/research/finrl/activation_smoke.py` and the RLlib/Ray Tune
  counterparts continue to write into `support/evidence/...` as default. This
  matches current task scope; if a future activation iteration runs the smoke
  inside a CI pod that does have the upstream packages, regenerate evidence and
  refresh the manifest.
- The OSS_INTEGRATION_CHECKLIST rows now use "evidence produced" language; once
  closeout transitions the task to `done`, updating the rows to reflect the
  closed lifecycle is fine. That is owner closeout work, not review scope.

## Decision

Approved. Returning the task to owner Claude2 for closeout per
`.orchestrator/skills/task-closeout-finalization.md`.
