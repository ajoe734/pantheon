# Starter Draft — Phase 5: Full Blueprint Gap Closure

Current rule: only `Codex` edits this file directly.

Last updated by: Codex

## Shared Draft

- Objective: absorb the unresolved phase4 operational-baseline work into one phase5 delivery frame and turn the remaining delivery gaps into a detailed machine-readable execution inventory, not just umbrella buckets. The current split is: service realization first, workbench packetization second, Lovable closeout third, and OSS + delivery foundation fourth, but the execution graph must open with multiple independent roots rather than serializing the whole phase behind one baseline task. (`docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/planning-session.json`; `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/execution-materialization.md`)
- Scope boundary: this round does not reopen phase2 `BG-*` semantic baseline work unless a reviewer shows the phase4 bridge inventory is wrong. The current bridge says phases 2-6 canonical baselines are archived done and the remaining gaps are operational: service exposure, command convergence, packet/workbench coverage, and OSS real integration. (`docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md:11-16,36-57`)
- Proposed architecture:
  - `runtime-control` owns side-effectful operator commands only; `governance-api` owns approvals, deployment plans, capital/runtime bindings, and evolution decisions/actions. (`docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/review-round-01.md:13-16`; `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/qwen-readout.md:20-22,32-38`)
  - `telemetry-ingest` and `lineage-read` are wrapper services over existing service classes, not new domain-invention work. (`services/telemetry/ingest_svc.py:1-20,51-67`; `services/telemetry/lineage_read/service.py:1269-1303`)
  - `bff` stays read-oriented and command-submitting, but wave 1 must remove snapshot/default seed mode from the normal integration path before we call the stack honest. (`services/control-plane/bff/read_store.py:43-49,102-121,175-204`; `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/claude-readout.md:130-132,158-160`)
  - Lovable remains a human-triggered UI accelerator; the current packet queue is execution inventory, not justification for publishing more packets before closeout. (`docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/consensus-packet.md:6-17`; `current-work.md:79-101`)
- Proposed wave order:
  1. `Wave 1 / Service and command-plane realization`: expand the old phase4 `SVC-*` bridge into `BP5-SVC-001` through `BP5-SVC-016`, covering registry/governance/deployment/capital/runtime/telemetry/lineage/incident/evolution/persona/BFF plus compose truth. (`docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/execution-materialization.md`)
  2. `Wave 2 / Workbench packetization`: convert the remaining Operator / Persona / Governance / Evolution / Research / Knowledge / Trainer / Consultation backlog into canonical packet families with backend-gap matrices (`BP5-WB-001` through `BP5-WB-008`). (`docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`)
  3. `Wave 3 / Lovable closeout`: close the current ready queue as real implementation loops, not just published handoffs, by running `BP5-LUV-001` through `BP5-LUV-010`. (`current-work.md:79-101`)
  4. `Wave 4 / OSS + delivery foundation`: realize OpenClaw, convert deferred OSS rows into executable paths, then land CI/CD and GCP baseline work through `BP5-OSS-*`, `BP5-CICD-*`, and `BP5-GCP-*`. (`OSS_INTEGRATION_CHECKLIST.md`; `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md`)
- Initial parallel dispatch roots:
  - `BP5-SVC-001` keeps the baseline/compose contract on `Codex`
  - `BP5-SVC-003` opens governance API realization on `Claude` without waiting for the registry split to be fully done
  - `BP5-OSS-001` opens OpenClaw source pinning on `Gemini`
  - `BP5-LUV-001` moves returned-feedback closeout to `Copilot`
  - `BP5-CICD-001` is root-ready as a second `Gemini` lane candidate instead of being blocked behind `BP5-SVC-001`
- Proposed task slices:
  - `BP5-SVC-001` to `BP5-SVC-016`: detailed service and command-plane realization inventory
  - `BP5-WB-001` to `BP5-WB-008`: detailed workbench packetization inventory
  - `BP5-LUV-001` to `BP5-LUV-010`: detailed Lovable queue closure inventory
  - `BP5-OSS-001` to `BP5-OSS-004`: detailed OSS realization inventory
  - `BP5-CICD-001` to `BP5-CICD-002`: CI/CD inventory
  - `BP5-GCP-001` to `BP5-GCP-002`: GCP foundation inventory
- Open disagreements:
  - Does phase5 explicitly absorb phase4's six `SVC-*` slices, or should phase4 be human-gated first before phase5 materializes anything? (`docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/execution-materialization.md:3-25`)
  - Is the first `runtime-control` container allowed to wrap the existing Flask `internal_api.py`, or must FastAPI parity be part of wave 1? (`services/control_plane/internal_api.py:11-20,26-32,112-130`; `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/claude-readout.md:131-133`)
  - Should Lovable queue closeout be capped before any new workbench packetization, given `0` `ui-done` and repeated `gh`-missing checkpoints? (`current-work.md:83-87,105-124`)
  - Do reviewers want `web` / `cron` inside the default single-VM profile, or held as optional until wave 1 compose is proven? (`docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md:11,18-23,42-46`)
