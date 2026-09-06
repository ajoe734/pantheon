# Legacy closeout deduplication and archive diagnosis

Evidence date: 2026-09-06 UTC. This is a coordinator's local report, not a merged repository deliverable or product acceptance. The operator approved the narrow existing-contract repair on 2026-09-06 after the explicit architecture discussion.

## Completed task-authority cleanup: PPL-ALLOC-009

Qualified local Human/Ops commands changed only this existing task's dispatch ownership, edit grants and handoff note. TaskStore event 3231 verified the result; event 3262 independently confirmed it remained in place.

- Status `todo`, owner `Human/Ops`, reviewer `Claude`, generation `4`. This is a temporary real non-worker ownership hold, NOT a claimed `blocked` lifecycle transition.
- Removed broad edit grants `services/control-plane/bff` and `execute-plans:src`; no source file was deleted or edited.
- Retained `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/archive`; added only `docs/deployment/evidence/PPL-ALLOC-009/evidence.json`.
- All five original acceptance strings and all seven original dependencies PPL-ALLOC-002 through PPL-ALLOC-008 were unchanged. Hosted causal journey, exact deployed identities, validation and residual risks remain required.
- No worker or pending delivery intent existed for this task. The qualified dispatch explanation rejected all seven configured workers as `task_not_dispatchable` after the cleanup.
- An attempted local Human/Ops `blocker` command was rejected by the actor allowlist before mutation. No identity was impersonated; the supported `note` operation recorded the actual ownership hold.

PPL-ALLOC-009 is historical hosted acceptance, not a second BFF/FE implementation owner. Source defects return to the existing scoped source owners. It predates `dev_bridge.work_class`: missing that field must NOT silently classify it as ordinary functional work. After OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001 has accepted source and qualified runtime, use its existing formal local policy path to establish typed pending authorization, then officially restore Antigravity/Claude. Any later real grant must bind the resulting current generation. No real MFA, hosted write, credential or capital action was authorized or executed here.

## PPL-ALLOC-007: source delivered, stale active row not yet reconciled

Live GitHub and current valid local repositories verified:

| Evidence | Exact identity |
| --- | --- |
| execute-plans PR 285, merged to dev 2026-07-13 03:59:01Z | head `1e5b1881d42a74a6234ce8ee3a83684c7d5076de`; merge `c62c0e8b9a49643c42f67614c542578afb233e84` |
| Pantheon PR 3490, merged to dev 2026-07-13 04:06:52Z | head `2f61fcea733ac830b21adbc516aae76eff8c922b`; merge `7c179f4d5124cf389af068551daed2441b0f694b` |
| Immutable committed PPL-ALLOC-007 archive | archived 2026-07-20 09:57:01Z; completed; byte SHA256 `cf95cc79c44a027f42046f83572029d07563a207fa1cc66a4685b755f7ce4c78` |

Both merge commits are ancestors of their respective current `origin/dev`. The merged Pantheon task brief records round-four approval, historical owner Codex/reviewer Claude, FE binding visibility/routing and FE-BFF gate run 29222175376. GitHub's native reviews list was empty; do not call this a native GitHub approval. This historical first delivery included route redirects; it is NOT acceptance of current first-release no-compatibility retirement or current hosted product readiness.

The archive has no explicit generation, normalized by the current contract to generation 1. Its stored task owner/reviewer are Codex/Codex2; its delivery evidence retains Codex/Claude. The archived reviewer reassignment proof is event `supervisor-reassign-50f84270dffe8135e267d193c794f5a12eb34daece145ca0ec55a7bcf1ad142e`, 2026-07-19 23:52:06Z. Historical obsolete filesystem paths in archived evidence are history only, never current runtime targets.

The active row is blocked Codex2/Claude, generation 2, with an old 2026-07-11 workspace blocker. Its title, phase, dependencies, dependency tracks, artifacts, acceptance, target repository and task class exactly match the archive: zero scope-field differences.

The qualified audited-reassignment reader verified event `human-ops-task-reassigned-96fa58802a213105b077ce9d3f3fb9e08237b81f33d5f5763ed2f7f43e743ae6`, 2026-09-05 14:48:18Z: Human/Ops moved owner Codex to Codex2, reviewer Claude unchanged, old_generation 1 to generation 2. Its explicit reason was terminal-quota recovery so an evidence-recovery follow-up could progress. This supports a role-only recovery diagnosis; it does not by itself prove every intermediate import/role transition. The implementation must verify the complete chain and fail closed if it cannot.

## Existing contract limitation, not permission to bypass it

Current qualified runtime `dd3f0563a6a3f9ca2976a354de29221d91665a73` and inspected origin/dev use the same existing recovery logic:

- `reconcile_merged_done` validates exact merged evidence and audited role provenance, but same-delivery immutable-archive recovery requires equal active/archive generations.
- `retire_archive_collision` requires equal generations and a distinct completed replacement. There is no truthful replacement task for this same-delivery case.
- `record_terminal_fact` rejects an existing active row; archive reconciliation is not an active-collision bypass.

No PPL-ALLOC-007 reconciliation, generation reset, archive rewrite, terminal-fact fabrication or hand-edited canonical JSON was performed. It remains held pending a tested, reviewed and qualified existing-contract extension.

## Approved structural repair and remaining acceptance

Create one governed source prerequisite extending existing TaskStore/CLI/archive proof and recovery boundaries. Require exact historical merged delivery, unchanged full scope, complete authenticated import and role/generation lineage, absence of new delivery and active execution, immutable archive preservation, transactional retirement of only the stale row, and prevention of repeated resurrection. Never claim generation 2 performed or completed a new implementation. No second reconciler, store, queue, cron, compatibility API or permission bypass.

The earlier whole-catalog overlap audit found 44 dependency-serialized overlapping pairs and zero simultaneous active-worker overlaps. Seven unsequenced pairs involved legacy PPL-ALLOC-007/009. Removing PPL-ALLOC-009's broad grants resolves its five pairs; PPL-ALLOC-007's two historical FE ownership overlaps remain unresolved until the approved recovery is actually accepted. Do not report global deduplication complete yet.

At 07:05 UTC the execution-authorization prerequisite's first exact-head review rejected eight real acceptance defects, including isolated unauthorized adapter launch after revocation and missing worker-entry gates. Owner corrective work resumed. Do not promote that rejected head or use it to admit privileged tasks. Registry and FE protocol also remain under corrective development. Twelve loops, Management/Agora, exact FE/BFF hosted pair and rollback remain unaccepted.
