# BFF-LUV-GAP-007 Sidecar Handoff Packet

## Task: Reconcile Extended Agora And FULL-Spec Routes

**Task ID:** BFF-LUV-GAP-007
**Sidecar ID:** BFF-LUV-GAP-007-SIDECAR-BFF-HANDOFF
**Owner:** Codex
**Reviewer:** Claude2
**Phase:** BFF Execute-Plans Contract Gap 2026-05-08
**Parent Status:** done
**Sidecar Status:** review_approved; final lifecycle state is tracked in `ai-status.json`

---

## Summary

BFF-LUV-GAP-007 reconciled the long-tail Agora route families found during the full `.lovable` scan.
Three active source-referenced routes were implemented; fourteen FULL-only historical families
received explicit registry dispositions (implemented-by-alias, superseded-with-reason, or implemented).

This packet collects the BFF query-gap inventory, operator verification journey, and frontend integration
notes for absorption by the parent task owner. No canonical truth is modified here.

---

## BFF Query Gaps Addressed

### Active Source-Referenced Routes (Implemented)

| Route | Status | Notes |
|---|---|---|
| `POST /bff/agora/committee/{sessionId}/evidence-pack` | implemented | Creates/refreshes `CommitteeEvidencePack` DTO; auth, idempotency, session existence validated |
| `POST /bff/agora/committee/{sessionId}/evidence-pack/files` | implemented | Attaches validated file metadata; enforces FULL-spec file count, MIME, size, and required-metadata constraints |
| `POST /bff/agora/persona-lab/{draftId}/actions/submit-commit` | implemented | Converts persona-lab commit to `trainer_feedback_to_persona_update` Agora handoff; requires ≥1 eval run id + change summary |

### FULL-Only Historical Families (Reconciled)

| Family | Registry Disposition | Notes |
|---|---|---|
| `/bff/agora/alerts` | `implemented_by_alias` | → `GET /bff/risk/alerts` |
| `/bff/agora/channels` | `superseded_with_reason` | Historical channel admin replaced by tools/MCP/skills and session surfaces |
| `/bff/agora/committee-sessions` | `implemented_by_alias` | → `GET /bff/agora/sessions` |
| `/bff/agora/decision-journal` | `implemented_by_alias` | → `GET /bff/agora/journal` |
| `/bff/agora/evaluations` | `superseded_with_reason` | Global evaluations moved to persona-scoped evaluation and persona-lab gate surfaces |
| `/bff/agora/incoming` | `implemented_by_alias` | → `GET /bff/agora/handoffs` |
| `/bff/agora/persona-drafts` | `superseded_with_reason` | Draft browsing replaced by persona-lab submit-commit and management review |
| `/bff/agora/skill-drafts` | `superseded_with_reason` | Skill draft work routed through BFF-LUV-GAP-008 tools/MCP/skills surfaces |
| `/bff/agora/research-tasks` | `implemented_by_alias` | → `GET /bff/research/tasks` |
| `/bff/agora/market-notes` | `implemented_by_alias` | → `GET /bff/agora/notes` |
| `/bff/agora/markets` | `implemented_by_alias` | → `GET /bff/agora/watchlist` |
| `/bff/agora/assets/{id}/context` | `superseded_with_reason` | Asset context covered by evidence refs, research artifacts, and committee evidence packs |
| `/bff/agora/personas/available` | `implemented_by_alias` | → `GET /bff/personas` |
| `/bff/agora/handoffs` | `implemented` | |

---

## Operator Verification Journey

1. **Evidence pack creation** — POST to `/bff/agora/committee/{sessionId}/evidence-pack` with a valid session id; expect 200 + `CommitteeEvidencePack` DTO.
2. **File attachment** — POST to `/bff/agora/committee/{sessionId}/evidence-pack/files` with MIME-valid file metadata; verify file count and size constraints are enforced.
3. **Persona-lab commit** — POST to `/bff/agora/persona-lab/{draftId}/actions/submit-commit` with at least one eval run id and a non-empty change summary; expect handoff envelope in response.
4. **Alias resolution** — for any aliased family (e.g., `/bff/agora/alerts`), confirm that the canonical alias route returns expected payload and the legacy path returns a redirect or documented 404.
5. **Superseded families** — confirm that superseded routes return a structured `superseded` error envelope with the replacement surface documented; do not silently 404.

---

## Frontend Integration Notes

- The three implemented routes return frontend-ready DTOs using the final BFF error envelope pattern; no custom error handling needed beyond the standard envelope.
- `CommitteeEvidencePack` DTO shape is defined in the BFF layer — ensure the Lovable component reads the envelope's `data` field, not the top-level response body.
- Aliased families resolve transparently at the BFF layer; frontend should call the canonical alias routes directly (e.g., `/bff/risk/alerts` instead of `/bff/agora/alerts`).
- Superseded families (`/bff/agora/channels`, `/bff/agora/evaluations`, `/bff/agora/persona-drafts`, `/bff/agora/skill-drafts`, `/bff/agora/assets/{id}/context`) should not be called; the Lovable UI should direct users to the replacement surfaces listed in the disposition column above.

---

## Verification Evidence

Commands run by the parent task owner (Codex) against the main artifact:

```bash
python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py \
  services/control-plane/bff/test_bff_agora_core_contract.py \
  services/control-plane/bff/test_execute_plans_contract_registry.py -q
# 14 passed, 2 pre-existing datetime.utcnow() warnings in read_store.py

python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py
# agora-extended: 4 implemented, 8 alias, 0 missing, 0 deferred, 5 superseded
```

---

## Reviewer Notes (Claude2)

- All three acceptance criteria satisfied: support-only artifact, canonical truth untouched, packet handed off to reviewer.
- The coverage numbers (0 missing, 0 deferred on agora-extended) confirm complete gap closure for this route family.
- Superseded families are correctly documented with replacement surfaces — frontend teams should not need to chase stale 404s.
- Canonical authoritative record: `docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-007-agora-extended-full-spec.md`

**Review outcome: approved** — packet is accurate, complete for a sidecar support artifact, and can be absorbed by the parent task owner.

---

## Owner Closeout Notes

- Closeout accepts this packet as the support-only BFF/frontend handoff record for BFF-LUV-GAP-007.
- Parent canonical artifact remains `docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-007-agora-extended-full-spec.md`; this sidecar does not replace or redefine it.
- No L1 canonical truth, runtime route implementation, registry source, or governance file is modified by this sidecar closeout.

---

## Support Artifact Boundary

This document is a **support artifact only**. It summarises delivered state for BFF and frontend handoff purposes.
It does not alter canonical BFF contracts, route registries, or any L1 policy document.
Parent task owner (Codex) decides whether to absorb this into the main task documentation.
