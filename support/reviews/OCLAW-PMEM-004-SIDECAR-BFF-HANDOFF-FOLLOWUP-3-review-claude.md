# Review: OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

**Reviewer**: Claude
**Owner**: Codex2
**Verdict**: Approved

## Scope check

- Diff introduced by this task vs. `origin/dev` merge-base (`2eb2fa744`) touches
  exactly two files: `.orchestrator/task-briefs/oclaw_pmem_004_sidecar_bff_handoff_followup_3.md`
  and `support/sidecars/OCLAW-PMEM-004/OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
  (anchor commit `e1b42902c`).
- No canonical Memory Plane, runtime-profile, BFF, frontend, or governance
  files were touched. `Mutates Canonical: no` holds.
- `git diff --check` only flags Markdown hard-break trailing spaces (intentional
  line-break syntax in the sidecar doc header), not a content defect.

## Technical claim verification

Cross-checked every concrete route/function claim against
`services/control-plane/bff/main.py`, `services/control-plane/bff/assistant/routes.py`,
and `services/persona/runtime_profile.py` on this branch:

- `GET /bff/personas/{persona_id}/memory` (`main.py:40676`) still falls back to
  `getattr(read_store, "list_memory_updates_for_persona", None)` and returns an
  ordinary empty 200 when the reader is missing — confirmed, matches the
  packet's "must not infer empty list when source was not reached" framing.
- `GET /bff/personas/{persona_id}/runtime-profile` (`main.py:40393`) calls
  `build_persona_runtime_profile(...)` (`services/persona/runtime_profile.py:84`)
  — confirmed.
- `GET /bff/assistant/providers` with `auth_probe` query param exists at
  `assistant/routes.py:448` (mounted under `router = APIRouter(prefix="/bff/assistant")`
  at `assistant/routes.py:126`), and delegates to `OpenClawOpsClient.list_assistant_providers(...)`
  (`openclaw_ops_client.py:588`, also wired through `_assistant_provider_list` at
  `main.py:58653`/`58656`) — confirmed.
- `GET /bff/assistant/providers/usage-summary` (`main.py:38538`) and the quota
  `source: not_configured` sentinel (`main.py:34529`) — confirmed present.
- Reauth routes `POST /provider/reauth`, `GET /provider/reauth/{session_id}`,
  `POST /provider/reauth/{session_id}/code` all exist in
  `assistant/routes.py:572,630,671` — confirmed.

## Dependency-state note (non-blocking)

The packet's header lists `Parent Owner: Claude2`, but the live `OCLAW-PMEM-004`
record (`python3 scripts/ai_status.py show OCLAW-PMEM-004`) currently shows
owner `Antigravity` — the worktree `ai-status.json` mirror this packet was
drafted against is stale on that field. Similarly, the Parent Absorption
Checklist item "Consume accepted `OCLAW-PMEM-002` and `OCLAW-PMEM-003`
outputs" is only half current: `OCLAW-PMEM-003` is archived `done` (verified
in the prior FOLLOWUP-2 review), but live `OCLAW-PMEM-002` is `blocked`
(implementation merged in PR #3003 with 37 passing tests, but required dev
evidence for a `model=openclaw/{persona_id}` response is still missing).
Neither point changes the verdict: the packet already disclaims dependency
completion in §7 ("does not claim that dependencies are complete"), and this
is support material, not a canonical state claim. The parent owner should
re-confirm `OCLAW-PMEM-002` readiness independently before treating its
output as absorbable, rather than relying on this packet's checklist wording.

## Notes

- The packet correctly labels itself as a sketch/handoff, not canonical
  schema, and lists explicit non-claims (§7). No corrections required to the
  route/function claims; parent owner (currently `Antigravity` per live
  state) can absorb directly, with the dependency-state caveat above in mind.

LLM-Agent: Claude
Task-ID: OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
Reviewer: Claude
