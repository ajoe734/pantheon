# OCLAW-PMEM-004 Review — Claude

Task: `OCLAW-PMEM-004` ("BFF and Management runtime surfaces")
Owner: `Codex`
Reviewer: `Claude`
Parent: `OCLAW-PMEM-000`
Depends on: `OCLAW-PMEM-002`, `OCLAW-PMEM-003`

## Scope checked

- Commits `63c1e0658` (canonical persona memory surface), `7fcca1a9d`
  (provider truth DTOs), `8e86b4e66` (evidence record) on
  `task/OCLAW-PMEM-004`.
- `services/control-plane/bff/main.py`,
  `services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py`,
  `services/control-plane/bff/tests/test_management_nl_assistant_provider.py`.
- Task doc:
  `docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-004-bff-ui-runtime-surfaces.md`.
- Cross-checked against `docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/INDEX.md`
  and the `support/sidecars/OCLAW-PMEM-004/*` handoff-gate chain
  (FOLLOWUP-2 through FOLLOWUP-19).

## Independent verification performed

1. Reran `pytest -q services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py
   services/control-plane/bff/tests/test_management_nl_assistant_provider.py`:
   89 passed. Matches the recorded evidence.
2. Read `_retrieve_canonical_persona_memory` and `bff_get_persona_memory`:
   confirmed it calls `/api/memory/retrieve` only, never falls back to a BFF
   snapshot or workspace file, and returns a precise `memory_source.reason`
   (`memory_plane_unconfigured`, `memory_plane_access_denied`,
   `memory_plane_http_error`, `memory_plane_unavailable`,
   `memory_plane_invalid_response`) with matching tests. This satisfies
   acceptance criterion 1.
3. Read the `_assistant_provider_usage_summary` diff: it adds `provider_auth`,
   `live_smoke`, `reauth`, and `readiness` (with
   `mount_ready_is_sufficient: False`) per provider row. Good direction, but
   two gaps below.
4. Checked `/home/lupin/code/execute-plans` (the `frontend-checkout:` target
   named in this task's artifacts) for any OCLAW-PMEM-004-related commit or
   working-tree change: none exists. `src/management/components/openclaw/OpenClawLlmAuthPanel.tsx`
   still only reads `quota` and the older `/bff/assistant/provider/reauth`
   session flow; it does not reference `provider_auth`, `live_smoke`, or
   `readiness` anywhere.
5. Read `support/sidecars/OCLAW-PMEM-004/OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-19.md`
   (dated 2026-07-11, same day as this task's evidence commit). §1 states
   "Frontend dispatch remains `defer`" and the Composition Intake Bundle in
   §2/§6 is an unfilled template — it was never submitted or accepted. This
   task's own sidecar chain therefore confirms frontend dispatch has not
   happened.

## Findings

1. **Missing acceptance criterion: "persona dependencies."** Acceptance
   criterion 2 requires the LLM Auth panel data to separate "provider auth,
   live smoke, quota source, persona dependencies, and reauth flow state."
   `INDEX.md`'s wave description also calls this out explicitly ("personas
   depending on each provider"), and the sidecar gate template repeats it as
   "dependent-persona completeness" / "complete dependency inventory." No
   field for this exists anywhere in `_assistant_provider_usage_summary`,
   `_management_ai_empty_usage_row`, or any other touched surface, before or
   after this diff. This dimension is simply absent from the delivered DTO.

2. **New DTO fields are only asserted for one provider.** The added test
   assertions (`test_assistant_provider_usage_summary_aggregates_history_and_quota`)
   check `provider_auth`/`live_smoke`/`readiness`/`reauth` only on the
   `codex_cli` row. Acceptance criterion 4 requires coverage "for
   codex/claude/openclaw," and the sidecar gate explicitly calls for "Mixed
   Codex, Claude, and OpenClaw rows where auth, smoke, dependency
   completeness, quota, and usability differ without pool-wide flattening."
   `claude` and `openclaw` rows are untested for the new fields.

3. **No Management UI work was delivered, and the task doc's scope claim
   contradicts the task's own definition.** The task doc states: "The
   execute-plans rendering layer remains a cross-repository composition
   boundary. This Pantheon task delivers and verifies the BFF DTOs consumed
   by that layer; it does not materialize frontend source inside this
   repository." But:
   - `INDEX.md`'s execution-order row for this exact task says "Wire BFF
     **and Management UI surfaces** for runtime profile, provider pool
     health, memory, quota, and reauth state."
   - `frontend-checkout:src` is listed as a first-class artifact of this
     task in both `ai-status.json` and the dispatch script.
   - This repo has established precedent (e.g. `BFF-CONSOL-013`,
     `ASST-LLM-AUTH`, `OPENCLAW-LLM-AUTH-005/006`) of implementing and
     reviewing frontend changes directly in `ajoe734/execute-plans` as part
     of the same BFF+UI task, not deferring them.
   - This task's own 19-entry sidecar chain built a formal fail-closed
     "Composition Intake Bundle" gate specifically to authorize frontend
     dispatch, and the latest entry (same day) still records the gate as
     `defer` with an empty bundle. Declaring frontend "not this task's job"
     without completing that gate or filing the follow-on dispatch it
     describes leaves acceptance criteria 2–4 unmet in practice, not just
     unimplemented in this repo.

## Verdict

**Reopen — request changes.** Acceptance criterion 1 (canonical persona
memory retrieval) is solid and well tested. Acceptance criteria 2–4 are not
met:

- Add a persona-dependency count/list (with a precise unavailable reason
  when it cannot be resolved) to the provider truth DTO.
- Extend provider-truth tests to cover `claude` and `openclaw` rows, not
  just `codex_cli`.
- Either implement the Management LLM Auth panel wiring directly in
  `ajoe734/execute-plans` per established precedent, or formally complete
  and submit the Composition Intake Bundle in
  `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-19.md` to produce an
  explicitly tracked follow-on frontend dispatch task — then update this
  task's doc so the scope statement matches what was actually decided
  instead of unilaterally declaring the UI work out of scope.

Handing back to owner (`Codex`) for these changes before re-review.
