# AG-BE-ID-003 Followup-13 Sidecar Review

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` |
| Helper parent | `AG-BE-ID-003` |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex2` / `Codex` |
| Decision | `review_approved` |
| Review source | Active task state and `review_ready_dispatch` |
| Packet commit | `d307dbe94390a5afcf1edb16ceb3b4f80e7b6bca` |
| Packet PR | `#1996` merged at `fa4894300d79254ef45ef293a997c2b95285a4fa` |
| Dev base at review | `fa4894300d79254ef45ef293a997c2b95285a4fa` |
| Mutates canonical truth | `false` |

## Approval Notes

Codex approves the followup-13 sidecar packet with these reviewed facts:

1. The packet stays support-only. The task commit added only the task brief and
   `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13.md`;
   it did not change canonical truth, OpenAPI/source-of-truth contracts, BFF
   runtime code, route registries, governance, database migrations, OpenClaw
   adapter code, compatibility manifest source, or execute-plans source files.
2. Parent `AG-BE-ID-003` remains correctly blocked, waiting for `Claude`, on
   the servant-session type-contract decision. The packet does not approve,
   reopen, or implement the parent.
3. Both `agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` still define
   `ServantSessionCreateRequest` with only `intent`, `strategy_ref`, and
   `metadata`, plus `additionalProperties: false`. There is no public
   `session_type`, `sessionType`, or `session_kind` field.
4. The additive v1.2 bundle is treated as downstream contract context only; it
   does not resolve how BFF runtime or OpenClaw receives `interactive`,
   `trainer`, or `research_task`.
5. Targeted BFF runtime grep found no `servant/sessions`,
   `/bff/agora/servant/sessions`, `ServantSession`, or
   `OPENCLAW_UPSTREAM_DEGRADED` matches under `services/control-plane/bff`.
   The packet correctly preserves the runtime implementation blocker.
6. The frontend handoff is conservative. `AG-FE-ID-001` remains gated on
   blocked `AG-BE-ID-003`; execute-plans PR `#63` is still `OPEN` with failed
   `integration-gate`; the checked remote trees still lack the strict Agora
   shell/client target files except for the noted branch-dependent `types.ts`.
7. The operator journey and parent absorption gates are accurate: identity,
   capability, and servant ensure can be treated as limited support context,
   while create/message/stream/terminate servant-session controls stay disabled
   until the parent contract and runtime implementation land.
8. Focused verification passed during review:
   `python3 scripts/agora_schema_bundle.py --verify`;
   `python3 -m pytest scripts/test_agora_v1_2_bundle.py -q` with 5 passed;
   `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q`
   with 18 passed.

## Scope Boundary

This review approves support material only. It does not decide the
`ServantSessionCreateRequest` type representation, does not approve use of
hidden metadata as the type contract, and does not implement or unblock parent
`AG-BE-ID-003`.

The parent owner still needs reviewer approval from `Claude` for the public
type field, deterministic server-side derivation rule, or other explicit
contract decision before implementing servant-session runtime behavior.

## Owner Closeout Instruction

The approved packet is returned to `Codex2` for task closeout finalization.
Closeout should keep the review record and packet support-only, preserve the
blocked parent dependency, and then use the normal task PR flow before moving
the sidecar task to `done`.
