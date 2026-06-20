# AG-BE-SW-001 Followup-3 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper parent | `AG-BE-SW-001` — Agora strategy workshop BFF and frontend handoff |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Claude` / `Claude2` |
| Date | `2026-06-20` |
| Status | `ready for review` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change
L1 canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, persona or registry state, database migrations,
or execute-plans source files.

## 1. Purpose

This third followup packet narrows to two items not covered in depth by the
prior sidecar packets:

1. The `api/v1/trainer` route family, which is fully implemented in the BFF
   but has no frontend path helpers or execute-plans page adapter.
2. The `api/v1/committees` vs `/bff/agora/committee/sessions` route family
   distinction, which affects how the parent owner should scope the FE handoff.

It also provides a consolidated three-packet decision summary to give the parent
owner a single-page view of what is still open.

## 2. Sources Checked

| Source | Why |
|---|---|
| `support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF.md` | First packet; baseline BFF gaps and operator journey. |
| `support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Second packet; narrowed decision matrix, current frontend state. |
| `services/control-plane/bff/main.py` (lines 12420–13145, 14805–14872, 47863–48308) | `api/v1/trainer/*`, `api/v1/committees`, and `/bff/agora/committee/*` route implementations. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Still an empty placeholder; workshop routes remain in `main.py`. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | No `api/v1/trainer` or workshop route path helpers added since prior packet. |
| `/home/lupin/code/execute-plans/src/agora/pages/TrainerStudio.tsx` | Still seed-data and management approval only; no trainer session route calls. |
| `/home/lupin/code/execute-plans/src/agora/pages/CommitteeRoom.tsx` | Still local `seed` sessions, local persona mock responses, and direct `fetch()` to `COMMITTEE_EVIDENCE_ENDPOINTS`. |

## 3. New Observation: Trainer Session API Gap

The BFF implements a complete `/api/v1/trainer/sessions` route family that was
not surfaced in the first two sidecar packets:

| Route | Verb | BFF location (approx. line) | Purpose |
|---|---|---|---|
| `/api/v1/trainer/sessions` | `POST` | main.py:12420 | Create a new trainer session |
| `/api/v1/trainer/sessions` | `GET` | main.py:12478 | List trainer sessions |
| `/api/v1/trainer/sessions/{session_id}` | `GET` | main.py:12538 | Get session detail |
| `/api/v1/trainer/sessions/{session_id}/controls` | `GET` | main.py:12566 | Get session-level controls |
| `/api/v1/trainer/sessions/{session_id}/patch` | `POST` | main.py:12585 | Patch session state |
| `/api/v1/trainer/sessions/{session_id}/message` | `POST` | main.py:12635 | Send a message in the session |
| `/api/v1/trainer/sessions/{session_id}/preview` | `GET` | main.py:12695 | Get a preview evaluation |
| `/api/v1/trainer/sessions/{session_id}/preview` | `POST` | main.py:12736 | Create a preview evaluation |
| `/api/v1/trainer/sessions/{session_id}/rapid-eval` | `POST` | main.py:13145 | Start a rapid eval on the session |
| `/api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}` | `GET` | main.py:13232 | Get rapid eval result |
| `/api/v1/trainer/sessions/{session_id}/commit` | `POST` | main.py:13007 | Commit/finalize the trainer session |
| `/api/v1/trainer/sessions/{session_id}/discard` | `POST` | main.py:13079 | Discard the trainer session |
| `/api/v1/trainer/replay` | `GET` | main.py:12798 | List replay sessions |
| `/api/v1/trainer/replay/{session_id}` | `GET` | main.py:12854 | Get a specific replay session |

Current frontend state:

- `src/agora/pages/TrainerStudio.tsx` uses a local seed feedback queue,
  `bff.personas.list()` for persona population, and `mutations.createApproval()`
  for persona-update submit — it does not call any `api/v1/trainer` route.
- `src/lib/bff-v1/paths.ts` has no trainer session helpers.
- The execute-plans `paths.ts` has `evolutionMutationReview` and similar
  `api/v1/operator` helpers, but no `api/v1/trainer` helpers.

Handoff rule: **Do not wire `TrainerStudio.tsx` to `api/v1/trainer` routes
until the parent owner has confirmed that this route family is part of the
Agora strategy workshop surface** (and not a separate trainer service surface).
The `api/v1/trainer` routes are implemented in `main.py` alongside Agora
routes, but they use a distinct prefix, distinct session model, and distinct
commit/discard lifecycle. The parent must decide whether:

1. `TrainerStudio.tsx` is the primary consumer of `/api/v1/trainer/sessions`,
   or
2. The trainer session API is a different surface served by a different frontend
   entry point.

Until that decision is recorded, the frontend page should continue to render
its current local-session UI and not silently call trainer session routes.

## 4. Route Family Distinction: api/v1/committees vs bff/agora/committee/sessions

Two `committee` route families co-exist and are not interchangeable:

| Family | Prefix | BFF location | Purpose | Auth |
|---|---|---|---|---|
| Governance committee | `GET /api/v1/committees` | main.py:14805 | Read-only list of governance committee records from the read store. Returns `committee_board` surface state, `consensus_state`, sponsor decision, participant roster, and service handoff objects. | `require_read_role` |
| Governance committee detail | `GET /api/v1/committees/{committee_id}` | main.py:14854 | Detail view of one governance committee record via `_cw03_committee_projection`. | `require_read_role` |
| Agora committee sessions | `GET|POST /bff/agora/committee/sessions` | main.py:47863–47870 | Agora-native workshop committee session list/create. Tracks session status, participants, templates, and linked ask-channel events. | Agora identity + read role |
| Agora session lifecycle | `POST /bff/agora/committee/sessions/{id}/open|close` | main.py:47945, 47993 | Open or close an Agora committee session; emits SSE ask-channel events. | Agora identity + operator role |
| Agora memos | `GET|POST /bff/agora/committee/sessions/{id}/memos` | main.py:48051, 48080 | Agora committee memo lifecycle. Publish route creates a `consult_memo_to_management_review` handoff. | Agora identity |
| Agora evidence packs | `POST /bff/agora/committee/{id}/evidence-pack{/files}` | main.py:20906, 20963 | Attach evidence-pack metadata to an Agora committee session. JSON metadata model; not multipart binary. | Agora identity |

Frontend handoff implication:

- A page that renders governance committee state (sponsor decision, quorum, 
  consensus) should call `api/v1/committees`, not `/bff/agora/committee/sessions`.
- A page that creates, opens, closes, or posts memos to an Agora workshop
  committee session should call the `/bff/agora/committee/*` family.
- `CommitteeRoom.tsx` currently uses local seed state for both workflows; it
  does not call either route family. The page must be refactored against the
  correct route family before any live-mode claim.

The first sidecar and followup-2 noted the Agora committee session routes.
This packet adds the explicit clarification that `api/v1/committees` is a
separate read-only governance projection surface and must not be conflated with
the Agora workshop committee session lifecycle.

## 5. Consolidated Open Parent Decisions (all three packets)

The table below collapses the outstanding decisions from all three sidecar
packets into a single view:

| Decision | Source | Blocking what | Recommended path |
|---|---|---|---|
| D1: Generic `StrategyWorkshop` route vs current route family | Packet 1, 2 | Any FE page that models a "workshop" object | Decide: adopt current committee/training/evaluation/persona-lab routes as the workshop surface, or define `GET|POST /bff/agora/workshops` in a canonical task. |
| D2: Alias freeze (`/committee-sessions` vs `/sessions` vs `/committee/sessions`) | Packet 1, 2 | FE path builder choices in `paths.ts` | Choose one canonical FE path; mark others compatibility-only. |
| D3: DTO binding for broad OpenAPI object bodies | Packet 1, 2 | Typed FE client generation | Bind named schemas to request/response bodies, or publish an adapter DTO contract. |
| D4: Evidence upload truth (JSON metadata vs multipart) | Packet 1, 2 | Binary file upload UI | Reconcile OpenAPI `multipart/form-data` wording with BFF JSON metadata implementation. |
| D5: Persona-lab `{draftId}` vs `{run_id}`, `202` vs `200` | Packet 1, 2 | `PersonaLab.tsx` submit wiring | Freeze BFF parameter name and response status; update OpenAPI to match. |
| D6: CTA authority shape | Packet 1, 2 | Create/open/close/publish/submit write CTAs | Decide: role-based, route capability, object-state `allowedActions`, or backend authority block. |
| D7: Research plan generation route | Packet 1, 2 | Workshop-to-research-plan flow | Decide: new BFF write route, assistant route, or research service handoff. |
| D8: Trainer session API surface ownership | **Packet 3 (new)** | `TrainerStudio.tsx` live wiring | Decide: is `TrainerStudio.tsx` the primary consumer of `api/v1/trainer/sessions`, or is this a separate surface? |
| D9: `api/v1/committees` vs `/bff/agora/committee` routing in FE | **Packet 3 (new)** | `CommitteeRoom.tsx` live backend calls | Clarify which route family `CommitteeRoom.tsx` should call for governance-committee state vs workshop session lifecycle. |

None of these decisions require changes to canonical truth, OpenAPI, or runtime
code by the sidecar. All decisions are for the parent owner to record through a
canonical task, accepted consensus packet, or explicit implementation choice.

## 6. Safe Operator Journey (unchanged)

The safe operator journey described in followup-2 remains current. No new routes
were added to the workshop surface between followup-2 and this packet. Key
rules still in force:

- Pages without workshop adapters must not fall back to local seed in live/strict
  mode.
- Every write route must set `Idempotency-Key` in the header; do not put
  idempotency keys in the request body.
- Memo publish and persona-lab submit-commit create management handoffs, not
  runtime bindings.
- `CommitteeRoom.tsx` must not call `api/v1/committees` for Agora session
  lifecycle operations; those routes are read-only governance projections.
- No workshop UI may route broker orders, create `RuntimeBinding` objects,
  or mutate capital binding authority.

## 7. Suggested Verification

Scope check:

```bash
git diff --check -- support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-SW-001
```

Focused BFF test confirmation (same suite as prior packet):

```bash
python3 -m pytest \
  services/control-plane/bff/test_bff_agora_extended_contract.py \
  services/control-plane/bff/test_ask_003_committee_lifecycle.py \
  services/control-plane/bff/test_ask_004_memo_publish_contract.py \
  services/control-plane/bff/test_bff_agora_core_contract.py \
  services/control-plane/bff/tests/test_agora_router.py \
  -q
```

Expected scope check:

- Only this sidecar support artifact is authored by the task.
- No L1 canonical docs, OpenAPI, capability manifest, BFF runtime code,
  route registry, governance code, registry state, migration, or
  execute-plans files are changed.
- The packet does not claim `AG-BE-SW-001` is unblocked or complete.

## 8. Handoff

This packet is ready for `Claude2` review. It should be treated as support-only
decision material for the parent `AG-BE-SW-001` lane. Items D8 and D9 are new
observations not present in the prior two packets; the remaining items reproduce
the open decision list from followup-2 for completeness.

The packet does not modify any canonical contract. It does not unblock the
parent task. The parent owner must record each decision through a canonical
task, accepted consensus packet, or implementation choice before frontend live
adapter work is claimed against the workshop surface.
