# SD-CONSULT-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `SD-CONSULT-001` - Extract consultation red-team lifecycle domain service
**Parent Owner**: `Codex`
**Parent Reviewer**: `Codex2`
**Parent Status**: `review`
**Sidecar Task**: `SD-CONSULT-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-27`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 canonical truth,
> core contract truth, or runtime / registry / governance implementation. It
> packages a reviewer-facing acceptance checklist, dependency map, verification
> evidence, and handoff notes for the `SD-CONSULT-001` parent review.

## 1. Executive Summary

`SD-CONSULT-001` is in `review` with `Codex` as owner and `Codex2` as reviewer.
The parent implementation adds a first-class `services.consultation` domain
service for consultation / red-team lifecycle records. The service now owns:

1. consultation requests and lifecycle status transitions
2. committee participant assignment
3. debate transcript events
4. append-only evidence attachments
5. submitted and published consultation memos
6. immutable published memo persistence at the store boundary
7. governance gate handoff records carrying memo refs, evidence refs, trace id,
   and audit refs
8. audit events for the lifecycle transitions covered by parent acceptance

The original review findings in
`docs/reviews/2026-04-27-sd-consult-001-codex-review.md` are addressed by the
follow-up handoff in
`docs/reviews/2026-04-27-sd-consult-001-codex-handoff.md`. Revalidation on
2026-04-27 UTC confirms the current consultation package smoke paths and import
paths run successfully from the repo root.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable task board for parent / sidecar status, owner, reviewer, acceptance, and artifact paths |
| `.orchestrator/task-briefs/sd_consult_001_sidecar_acceptance.md` | Confirms this helper is support-only and must hand off to `Codex2` |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines the SD consultation gap and acceptance shape |
| `docs/reviews/2026-04-27-sd-consult-001-codex-review.md` | Records the initial review findings that required fixes |
| `docs/reviews/2026-04-27-sd-consult-001-codex-handoff.md` | Parent owner handoff claiming the fixes and verification commands |
| `services/consultation/models.py` | Defines service-owned request, memo, transcript, evidence attachment, audit, participant, and gate handoff records |
| `services/consultation/store.py` | Owns JSON-backed persistence, append-only evidence attachment behavior, audit log, and published memo immutability |
| `services/consultation/main.py` | Exposes the FastAPI lifecycle endpoints and emits audit events for transitions |
| `services/consultation/smoke_test.py` | Covers request lifecycle, participant assignment, evidence attach, transcript event, memo publish, immutability, handoff, and audit events |

## 3. Parent Acceptance Checklist

| Parent acceptance target | Evidence to review | Status now |
|---|---|---|
| Consultation request has service-owned lifecycle records | `ConsultRequest`, `CreateConsultRequest`, `/api/consult/requests`, `/submit`, status enum, and smoke lifecycle coverage | PASS |
| Committee debate has service-owned transcript records | `ConsultTranscript`, `TranscriptEvent`, `/api/consult/requests/{request_id}/events`, sequence numbering, and smoke event coverage | PASS |
| Red-team / committee memo has service-owned lifecycle records | `ConsultMemo`, `SubmitMemoRequest`, `/api/consult/memos`, `/publish`, target memo lookup, and smoke memo coverage | PASS |
| Memo publication is immutable or append-only | `ConsultationStore.put_memo()` rejects changes to already-published memos and appends first publication to `consult_memo_publications.jsonl` | PASS |
| Evidence attachment is append-only | `ConsultEvidenceAttachment` plus `put_evidence_attachment()` rejects duplicate attachment ids and request evidence refs are preserved | PASS |
| Governance gate handoff carries evidence refs and audit trace | `ConsultGateHandoff`, `CreateGateHandoffRequest`, `/api/consult/handoffs`, memo validation, evidence merge, trace id, and audit refs are covered in smoke | PASS |
| Lifecycle transitions emit audit events | `request_created`, `request_submitted`, `participant_assigned`, `evidence_attached`, `transcript_event_added`, `memo_submitted`, `memo_published`, and `gate_handoff_created` are asserted in smoke | PASS |
| Repo-root smoke imports are repaired | `python3 -m unittest services.consultation.smoke_test` and `python3 -m services.consultation.smoke_test` both pass | PASS |

## 4. Verification Evidence

Commands rerun from repo root on 2026-04-27 UTC for this sidecar packet:

1. `python3 -m unittest services.consultation.smoke_test` - PASS
2. `python3 -m services.consultation.smoke_test` - PASS
3. `python3 services/consultation/run_smoke.py` - PASS
4. `python3 services/consultation/run_smoke_logic.py` - PASS
5. `python3 -m py_compile services/consultation/main.py services/consultation/models.py services/consultation/store.py services/consultation/smoke_test.py services/consultation/run_smoke.py services/consultation/run_smoke_logic.py` - PASS
6. `python3 -c 'from services.consultation.main import app; print(app.title)'` - PASS, prints `Pantheon Consultation Service`

## 5. Dependency Map

### 5.1 Durable Task Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `SD-CONSULT-001` | parent task | Mainline consultation domain service implementation, currently in `review` |
| `SD-CONSULT-001-SIDECAR-ACCEPTANCE` | support helper | Acceptance and dependency packet only; does not mutate canonical truth |
| `SD-FND-001` | parallel foundation task | Future adoption should converge consultation lifecycle envelopes with shared foundation primitives when parent owner chooses to integrate |
| `SD-LIN-TRACE-001` | parallel lineage task | Consultation handoff and audit refs are potential lineage edges, but this sidecar does not define canonical lineage truth |
| `SD-SRC-EVIDENCE-001` | parallel source / evidence task | Consultation evidence attachments should eventually reference governed evidence bundle ids from that lane |
| `SD-RECON-001` | downstream residual task | Consultation audit and handoff records can become governance evidence for broader reconciliation closeout |
| `EP5-002-PACKET-PREP-001` | later proof packet prep | Gate handoff evidence and audit refs can support an operator proof packet after required dependencies and human gates |

### 5.2 Semantic Dependency Chain

| Dependency | Source | Why it matters |
|---|---|---|
| SD consultation gap definition | `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Requires service-owned request, debate transcript, memo, immutable publication, and gate handoff evidence |
| Initial review findings | `docs/reviews/2026-04-27-sd-consult-001-codex-review.md` | Establishes the four concrete fixes reviewer should recheck |
| Current parent handoff | `docs/reviews/2026-04-27-sd-consult-001-codex-handoff.md` | Lists the repaired import paths, handoff model, immutability rule, audit coverage, and verification commands |
| Service-owned persistence boundary | `services/consultation/store.py` | Prevents published memo overwrite and preserves append-only audit/publication records |
| API lifecycle boundary | `services/consultation/main.py` | Ensures BFF-visible consultation behavior is now expressed through a first-class domain service surface |
| Smoke coverage | `services/consultation/smoke_test.py` | Proves the parent acceptance path end-to-end without relying on prose-only claims |

## 6. Open Cautions for Review

| Caution | Why it matters |
|---|---|
| This sidecar is not the parent implementation | It only packages acceptance and dependency evidence for `Codex2` review |
| Gate handoff status remains a basic record | Parent acceptance requires carrying refs and audit trace, not full external gate delivery semantics |
| JSON-backed store is a local proof boundary | The implementation proves service-owned lifecycle semantics; durability hardening can stay in later foundation / storage work |
| Evidence refs are ids, not full evidence bundle validation | Integration with governed evidence bundles belongs with `SD-SRC-EVIDENCE-001` or future adoption work |
| Lineage read-model integration is not claimed | Handoff and audit refs are available for later lineage work, but this parent should not be reviewed as a completed lineage trace |
| Foundation envelope adoption is not claimed | Shared TraceContext / CommandEnvelope adoption belongs to `SD-FND-001` and follow-on adoption tasks |

## 7. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating this packet as canonical SD-05 policy truth | This file is support material only |
| Requiring this parent to complete shared foundation adoption | That belongs to `SD-FND-001` / adoption follow-ups, not the narrow consultation service extraction |
| Treating gate handoff creation as proof of external governance gate acknowledgement | The current handoff record carries evidence and audit refs; external acknowledgement lifecycle is not part of parent acceptance |
| Treating evidence attachment ids as governed evidence validation | The consultation service records refs; source / evidence validation is a separate lane |
| Mutating L1 docs, core contract truth, runtime registry, or governance implementation from this sidecar | Sidecar scope explicitly forbids canonical or runtime implementation changes |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates only `support/sidecars/SD-CONSULT-001/SD-CONSULT-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited by sidecar | PASS | No L1 policy docs, contract docs, runtime registry, or governance implementation files were modified in this helper slice |
| Parent acceptance mapped to repo-current evidence | PASS | Sections 3 and 4 tie each acceptance item to concrete model, store, API, smoke, and command evidence |
| Dependency chain is explicit | PASS | Section 5 maps parent, sidecar, parallel SD foundation / lineage / evidence lanes, and later EP5 proof packet relevance |
| Review caveats are bounded | PASS | Sections 6 and 7 separate parent acceptance from future hardening, evidence validation, lineage integration, and external gate acknowledgement |

## 9. Handoff to Reviewer (`Codex2`)

This sidecar is ready for reviewer use as the acceptance / dependency packet for
`SD-CONSULT-001` in its current `review` state.

What it gives you now:

1. a checklist that maps each parent acceptance criterion to concrete repo
   evidence
2. a verification list rerun from repo root on 2026-04-27 UTC
3. a dependency map showing which nearby SD lanes can consume the consultation
   service evidence later without treating this sidecar as canonical truth
4. review guardrails that keep this helper support-only and prevent overclaiming
   lineage, evidence validation, external gate acknowledgement, or foundation
   adoption

Recommended reviewer stance now:

1. approve the sidecar if the packet accurately reflects the parent review
   surface and support-only boundary
2. review the parent task against the concrete service evidence in
   `services/consultation/`
3. keep any extra durability, envelope adoption, source/evidence validation, or
   lineage integration asks as follow-up work unless they are required by the
   parent acceptance text

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`SD-CONSULT-001`. This file is a support artifact and does not modify canonical
truth.*
