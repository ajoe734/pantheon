# Lovable Route-Live Activation Prompt

Use this prompt when resuming the remaining front-owned route-live activation work in
`front-ai-trading-system`.

```text
Implement the remaining route-live Pantheon UI surfaces in `front-ai-trading-system`.

Important operating rules:
- Pantheon has already confirmed these features are on the live-route side.
- Do not treat them as pending-BFF or generic blocked-shell work.
- Use only the existing BFF client and canonical Pantheon handoff artifacts.
- Do not import demo providers, local mock adapters, or client-derived fallback semantics.
- If any required field is missing from the live payload, stop and emit the canonical `bff-gap` handoff for that feature instead of inventing state client-side.
- When a feature is complete, publish `ui-done` and `frontend-feedback` from one Git-visible commit.
- Do not stop at `ui-done` only. Pantheon supervisor will not truthfully close the feature until the feedback bundle and `frontend-feedback` return are both visible through GitHub sync.
- Keep the same exact `source_commit` in the `ui-done` and `frontend-feedback` handoffs for one reviewed slice.

Implement the following features:

1. `RW-05-artifact-compare`
- Build the artifact registry, artifact detail, and artifact compare surfaces.
- Use:
  - `GET /api/v1/artifacts`
  - `GET /api/v1/artifacts/{artifact_id}`
  - `GET /api/v1/artifacts/compare`
- Do not derive diffs, ancestry, or compare summaries client-side.
- Render compare output from Pantheon BFF only.

2. `KW-02-research-notes`
- Build research-notes list, detail, and create surfaces.
- Use:
  - `POST /api/v1/knowledge/notes`
  - `GET /api/v1/knowledge/notes`
  - `GET /api/v1/knowledge/notes/{note_id}`
- Do not infer owner identity, attachment labels, or route targets from raw ids.
- Use BFF-authored `display_name`, `display_label`, and `route_href` fields.

3. `KW-03-evidence-refs`
- Build evidence reference list and detail surfaces.
- Use:
  - `GET /api/v1/knowledge/evidence`
  - `GET /api/v1/knowledge/evidence/{ref_id}`
- Do not construct URLs from `source_ref`, `storage_ref`, or raw ids.
- Render credibility and linked-object semantics exactly as returned by the BFF.

4. `KW-04-insight-cards`
- Build insight-card list and detail surfaces.
- Use:
  - `GET /api/v1/knowledge/insights`
  - `GET /api/v1/knowledge/insights/{insight_id}`
- Do not synthesize cards, confidence, supersession, or filter vocabularies in the browser.
- Render backend-owned filter metadata and scope context directly.

5. `KW-05-strategy-spec`
- Build strategy-spec list, detail, version-history, and compare surfaces.
- Use:
  - `GET /api/v1/knowledge/strategy-specs`
  - `GET /api/v1/knowledge/strategy-specs/{strategy_id}`
  - `GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions`
  - `GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare`
- Do not reconstruct version ancestry or diff raw spec JSON client-side.
- Use canonical route hrefs and backend-generated compare output only.

6. `CW-02-debate-transcript`
- Build the consultation debate transcript surface.
- Use:
  - `GET /api/v1/consultations/{session_id}/transcript`
- Preserve backend ordering exactly by `sequence_no`.
- Do not turn this into a local chat state machine.
- Render actor identity, redaction, and inline evidence links from the BFF payload only.

7. `TW-02-parameter-controls`
- Build the trainer parameter-controls surface.
- Use:
  - `GET /api/v1/trainer/sessions/{session_id}/controls`
  - `POST /api/v1/trainer/sessions/{session_id}/patch`
- Do not invent control ranges, client-side clipping, or synthetic diff summaries.
- Render accepted and rejected patch responses from backend-shaped fields only.
- Respect `allowedActions.canPatchControls` and degraded-state gating.

Per-feature references:
- `docs/pantheon-handoffs/<feature>/FRONTEND_CHANGE_SPEC.md`
- `.coordination/responses/<feature>-lovable-ui-task.yaml`
- `.coordination/responses/<feature>-lovable-prompt.md`
- `docs/bff/<feature>.md`
- `docs/examples/<feature>.json`

Completion contract:
- If you finish a feature, write:
  - `.coordination/requests/<feature>-ui-done.yaml`
  - `.coordination/requests/<feature>-frontend-feedback.yaml`
- Refresh the feedback bundle too:
  - `docs/pantheon-feedback/<feature>/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/<feature>/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/<feature>/UI_DECISIONS.md`
  - `docs/pantheon-feedback/<feature>/QA_STATUS.md`
- Keep `source_commit` truthful and GitHub-visible.
- Sync the `ui-done`, feedback bundle, and `frontend-feedback` files back to GitHub and stop.
- Pantheon supervisor polls the coordination/GitHub-visible loop on a fixed cadence and will either close the task or emit the next follow-up packet.
- If a required field is missing, write `.coordination/requests/<feature>-bff-gap.yaml` and stop that feature.
```
