# BP5-SVC-016 Review Packet

**Sidecar Kind:** review_packet
**Helper Parent:** BP5-SVC-016
**Prepared by:** Claude
**Reviewer:** Codex
**Prepared at:** 2026-04-16
**Parent Task Status:** done (review-approved and closed)
**Parent Commit:** `b66fa0788d4afac935cf91cff3c90f15e8e1fadc`
**Commit Subject:** `BP5-SVC-016: package honest service stack topology`

> **Scope note:** This is a support-only artifact summarising the review evidence for BP5-SVC-016.
> No canonical truth files were modified. This packet is advisory input for downstream consumers
> and records the formal review outcome for audit purposes.

---

## 1. Review Outcome

| Field | Value |
|-------|-------|
| Reviewer | Claude |
| Review date | 2026-04-16 |
| Review file | `support/sidecars/BP5-SVC-016/BP5-SVC-016-REVIEW.md` |
| Outcome | **Approved — both acceptance criteria met, no remaining findings** |

### Reviewer Verdict (Claude)

> "APPROVED. Both acceptance criteria are met. The implementation is clean and honest — no
> local-only fallbacks, no hidden state, topology-verified via smoke."

Key review findings (all passing):

1. All required surfaces present in `docker-compose.yml`: `signal-store`, `runtime-manager`,
   `governance`, `telemetry`, `incidents`, `postmortems`, `operator-bff`.
2. Dependency ordering is correct: `telemetry → runtime-manager`; `incidents → runtime-manager + telemetry`;
   `postmortems → incidents`; `operator-bff → all five upstream services`.
3. Health checks use `service_healthy` conditions throughout — no optimistic boot ordering.
4. `BFF_READ_SURFACE_STATE: fresh` enforces honest mode; no snapshot fallback path is wired in.
5. `smoke-stack` service is correctly gated under the `smoke` profile — not started by default.
6. `scripts/smoke_honest_stack.py` exercises the full canonical path via HTTP only: health checks →
   governance write-authority → runtime binding creation → telemetry ingest → incident creation →
   postmortem creation → BFF honest-mode guidance check → SSE replay verification.
7. Cross-process IncidentStore shared via volume mount (`incident-data:/data/incidents`) is consistent
   with single-VM baseline; multi-VM deployment would replace this with HTTP calls (out of scope).

---

## 2. Delivered Artifacts

| File | Role |
|------|------|
| `docker-compose.yml` | Full service topology with health checks, dependency ordering, and `BFF_READ_SURFACE_STATE: fresh` |
| `Dockerfile.smoke` | Minimal smoke runner image (stdlib-only, no pip requirements) |
| `services/control-plane/bff/Dockerfile` | BFF container definition |
| `services/governance/Dockerfile` | Governance service container definition |
| `services/telemetry/Dockerfile` | Telemetry service container definition |
| `services/incidents/Dockerfile` | Incidents service container definition |
| `services/postmortems/Dockerfile` | Postmortems service container definition |
| `services/runtime-manager/Dockerfile` | Runtime-manager container definition |
| `scripts/smoke_honest_stack.py` | End-to-end smoke script covering all six surfaces (229 lines) |
| `docs/remote-dev-gcp-vm.md` | "Honest Service Stack" section with compose up, health check, and smoke run commands |

---

## 3. Acceptance Criteria Verification

| AC # | Criterion | Evidence | Status |
|------|-----------|----------|--------|
| AC-1 | runtime, governance, evidence, BFF, and streaming surfaces boot through one honest compose topology | `docker-compose.yml` defines all seven services with `service_healthy` dependency ordering and no snapshot fallback path; `BFF_READ_SURFACE_STATE: fresh` is hardcoded. | **MET** |
| AC-2 | smoke scripts prove the stack can start without hidden local-only fallbacks | `scripts/smoke_honest_stack.py` exercises governance, runtime, telemetry, incident, postmortem, BFF guidance, and SSE replay paths via HTTP only — no local file reads or mocked responses. | **MET** |

---

## 4. Dependency Chain Evidence

BP5-SVC-016 closes the compose topology layer on top of all six service-realisation tasks. Their
final status at time of review:

| Dependency | Status | Delivery Commit | Key Surface Contributed |
|------------|--------|-----------------|-------------------------|
| BP5-SVC-002 | done | `7e7cff4` | Registry split-API (`artifact_state` / `deployment_stage`) |
| BP5-SVC-003 | done | `0031b89` | Governance API and `ApprovalDecision` audit flow |
| BP5-SVC-005 | done | `37ed952` | Deployment orchestration saga with outbox/inbox |
| BP5-SVC-009 | done | `8fc304d` | Telemetry ingest and shock-absorption path |
| BP5-SVC-010 | done | `38aa953` | Lineage read-model and performance service HTTP surface |
| BP5-SVC-015 | done | `fd497d0` | BFF fallback removal and honest `fresh` surface enforcement |

All six dependencies are in `done` state; no blocking open questions remain for the compose layer.

---

## 5. L1 Contract Alignment

| L1 Document | Alignment |
|-------------|-----------|
| `TARGET_ARCHITECTURE.md` | All six canonical service surfaces are represented in the compose topology. |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | `BFF_READ_SURFACE_STATE: fresh` enforces the policy that the BFF must not fall back to a stale snapshot in normal operation; this matches the policy's "honest surface" requirement. |
| `PAPER_CANARY_LIVE_POLICY.md` | Smoke script exercises the governance write-authority path before any deployment stage transitions are tested — consistent with the `artifact_state=approved` gate requirement. |
| `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` | Telemetry ingest is exercised as a standalone HTTP write in the smoke sequence without bypassing the service boundary. |
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | `signal-store` service included in compose topology; kill-switch surface is network-reachable. |

---

## 6. Open Questions for Downstream Consumers

These are scoping notes for future tasks — not retroactive blockers for BP5-SVC-016.

| OQ # | Question | Owner | Priority |
|------|----------|-------|----------|
| OQ-1 | All services use in-memory storage. Persistent backend (SQLite/Postgres) sidecars are not included. GCP deployment tasks (BP5-GCP-001/002) must decide whether to introduce a persistence layer or continue with in-memory for the non-prod baseline. | Gemini / GCP lane | Medium |
| OQ-2 | `PANTHEON_RUNTIME_MANAGER_TOKEN: runtime-control-internal` is a hardcoded dev token. Production secret management must be handled in BP5-GCP-001/002 or a dedicated secrets task — not in this topology. | Gemini | Medium |
| OQ-3 | Compose topology is single-VM. For multi-VM deployment, shared-volume IncidentStore mounts must be replaced with HTTP-based inter-service reads. This is an architectural seam to track in BP5-GCP-002. | Gemini | Low |
| OQ-4 | `scripts/smoke_honest_stack.py` URL resolution uses `localhost` fallbacks when env vars are absent. In a CI environment that runs compose in a container network, the service names must be injected via env. CI pipeline (BP5-CICD-002) should wire these. | CI lane | Low |

---

## 7. Companion Sidecar Artifacts

| Artifact | Path |
|----------|------|
| Formal review file | `support/sidecars/BP5-SVC-016/BP5-SVC-016-REVIEW.md` |

---

## 8. Handoff Notes for Codex

1. **AC-1 and AC-2 are both MET.** The honest compose topology is complete and review-approved.
2. **All six dependency tasks (BP5-SVC-002/003/005/009/010/015) are in `done` state.** No unresolved
   blockers propagate to BP5-SVC-016.
3. **OQ-1 and OQ-2** (persistence and secret management) are the primary open questions for the GCP
   deployment wave; they do not retroactively block BP5-SVC-016.
4. **OQ-4** (CI env-var injection for smoke URLs) is a low-priority note for BP5-CICD-002.
5. This packet may be absorbed into the main delivery evidence or linked from the parent task archive;
   that decision is with the parent owner.
6. No canonical truth files were created or modified by this sidecar.

---

*Support artifact only. Do not edit L1 canonical files based on this document.*
