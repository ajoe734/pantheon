# LLM and OSS simplification execution — 2026-09-06

## Operator scope and delivery

The operator requested: 「把整批建議接成可執行任務」 and then explicitly included completing the missing task coverage, actual LLM replacement / OSS upgrade delivery, and the related existing implementation work: 「這些也要補完阿」. This plan operationalizes all sixteen candidates and the full dependency inventory in the merged audit, PR #5621, commit 70e7abadaa4800f6d58acbbe3189a76c932d149d. The audit baseline is Pantheon 471dc5391a0f9cbde54d51730891583043708e42 and execute-plans 5d4f385284b44a30e10764426a47fd808a7ae3cb; execution must rebase and inspect current dev.

Claude / Claude2 and Antigravity implement. Codex / Codex2 perform independent review. The existing supervisor dispatches work and the existing integrator delivers reviewed commits. This plan changes no credentials, model settings, account grouping, quotas, slot counts, or provider configuration. Existing Registry and FE release protocol workers retain their scopes and review findings.

## Work ownership

The complete task definitions, dependency graph, artifact grants, and candidate mapping accompany this plan in `tasks.json` and `COVERAGE.md`. They are task specifications, not hand-edited canonical state. Only the trusted signed local bridge and qualified Human/Ops status CLI materialize tasks or record coordination notes.

Reuse each existing owner for work already within its acceptance. A Human/Ops note may point that owner at exact audit evidence; it does not rewrite its immutable acceptance. New residual tasks wait for the existing owner to merge, inspect its actual diff and evidence, and implement only the remaining requirement. They must never redo accepted work merely to produce a new PR, fabricate a deletion count, or retain permanent duplicate implementations. If an upstream task already satisfied a particular requirement, record its exact source and test proof in the downstream disposition ledger.

Shared source files and dependency manifests have one active writer. Dependencies serialize overlapping grants. An unrelated read-only inventory/benchmark may proceed concurrently. Read access to code is not an edit grant. Frontend paths belong to the separate ajoe734/execute-plans repository, based on dev; no frontend source may be copied into Pantheon. New requirements discovered outside a task's declared artifacts return for a qualified scope handoff, preserving other owners' acceptance.

## Functional scope and decisions

These packets authorize source development, isolated local functional tests, CI, documentation, dependency resolution, and reviewed dev merges. They do not authorize production/hosted mutation, real trading or capital changes, account or privilege changes, credential rotation, deletion/migration of production data, or uploading private corpora to a new hosted retrieval provider. Existing privileged hosted tasks and their authentic authorization requirements remain separate. A source merge is not proof of deployment.

For source-compatible OSS changes, implement and validate the latest stable compatible release identified at execution time from primary upstream sources. Every retained older version needs a concrete incompatibility or acceptance constraint, official evidence, an owner, and a next action; “not needed” without evidence is not closure. Do not choose prereleases, assume a floating tag proves an installed version, or force all scientific packages into one interpreter. Refresh the September 6 audit inventory against actual current dev before removing a declaration.

For LLM or retrieval replacement, establish and freeze a baseline and holdout before tuning. Use the currently authorized model/auth path and verify actual capabilities. A route called Responses does not by itself prove JSON-schema output support. Prefer validated structured extraction or a pure data-emission tool; no domain mutation is hidden in extraction. Preserve provenance, refusal/incomplete behavior, tenant isolation, and deterministic numerical and ledger authority. Select one local retrieval backend with measured quality and operational cost; no mock vectors may be presented as real semantic search.

Research framework retirement, storage selection and other conditional candidates must produce a tested decision and implement the source changes justified by that evidence. A failed quality gate must be reported honestly with exact causes and retained behavior; the worker must attempt bounded corrections within scope rather than marking a plan or unsupported replacement complete. External decisions remain explicit unresolved work with a concrete reviewable option, never fabricated approval.

## Acceptance and completion

Each implementation uses a clean task worktree from current dev, stages only declared source and evidence, runs meaningful actual behavior tests, commits with genuine required task/author/reviewer trailers, pushes a PR, obtains independent exact-head canonical review and required CI, and merges through the existing integrator. Review findings are corrected by the implementation owner; no local-only result or open PR is complete.

Evidence records before/after source identities; deleted files, functions, branches, dependencies and deployment units; preserved contracts; actual test commands, exit codes and executed counts; and relevant quality, cost and latency comparisons. Collection-only checks, missing-dependency skips, fabricated success or weakened assertions cannot satisfy acceptance. A numerical tool remains responsible for pricing/Greeks, risk and execution. Existing authentic auth/privacy tests must execute when dependencies are removed.

The final closure task joins all sixteen candidates, every inventory row, all accepted implementation PRs, and current integrated source validation. It separates implemented, evidence-supported retention, and genuinely blocked items. A task catalog or inventory report is not overall LLM/OSS implementation completion. Hosted readiness remains a separate exact-version release obligation.
