# AG-BE-RS-004 Sidecar Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `AG-BE-RS-004-SIDECAR-REVIEW`
**Helper parent:** `AG-BE-RS-004` - Evidence/result synthesis skill
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Codex`
**Sidecar reviewer:** `Claude`
**Date:** `2026-06-21`
**Sidecar status at packet time:** `in_progress`
**Parent status from ai-status at packet time:** `review_approved`

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, OpenAPI or JSON schema truth, runtime implementation,
> registry behavior, governance behavior, routing, or parent task lifecycle
> state. It packages evidence for the parent owner/reviewer and gives the
> assigned sidecar reviewer a compact approval surface.

## 1. Purpose

This packet gives `Claude` a compact review/handoff surface for
`AG-BE-RS-004`. It records:

1. the parent acceptance surface and lifecycle facts
2. GitHub PR/check evidence for the merged parent implementation
3. local focused validation run by this sidecar
4. code evidence for evidence grounding and VersionPatchProposal guardrails
5. residual closeout notes that remain parent-owned

This sidecar does not approve, reopen, or finalize `AG-BE-RS-004`. Parent
closeout remains with the parent owner and the normal `review_approved -> done`
flow.

## 2. Parent Task Summary

| Field | Value |
|---|---|
| Parent task | `AG-BE-RS-004` |
| Title | Evidence/result synthesis skill |
| Owner / reviewer | `Claude` / `Codex` |
| Status from `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-004` | `review_approved` |
| Parent PR | `https://github.com/ajoe734/pantheon/pull/2096` |
| PR state from GitHub | `MERGED` |
| PR base / head | `dev` / `task/AG-BE-RS-004` |
| PR head at merge | `2cc0a1d88629d28416a7d0bacc47ee7f572067e4` |
| Merge commit | `9cb0158f4f8902be620ecd4326a4884754e92c21` |
| Merged at | `2026-06-21T15:39:52Z` |
| PR file count | 5 files, 1336 insertions |

Parent acceptance summary:

- Implement the Pantheon-side `agora-result-synthesis` skill under
  `integrations/openclaw/skills/agora/result_synthesis/`.
- Synthesize ResearchRunSummary plus ConsultMemo evidence into discussion
  output without ungrounded conclusions.
- Require non-empty input research run refs and evidence refs.
- Filter output `evidence_refs` to the caller-provided input evidence scope.
- Block non-`insufficient` verdicts when scope-filtered evidence refs are empty.
- Treat stub/fixture results as non-production proof and downgrade stub-only
  `promising` verdicts.
- Validate each proposed version patch against the v1.3
  `VersionPatchProposal` schema.
- Preserve consult disagreements in `unresolved_decisions`.
- Do not create routes, enums, allowlist expansion, RuntimeBinding writes,
  capital binding, broker orders, or live enable behavior.

## 3. Evidence Map

### 3.1 Lifecycle and PR evidence

| Evidence | Result | Source |
|---|---|---|
| Sidecar task is active and support-only | `AG-BE-RS-004-SIDECAR-REVIEW`, owner `Codex`, reviewer `Claude`, artifact path under `support/sidecars`; `mutates_canonical=false` | `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-004-SIDECAR-REVIEW` |
| Parent task is approved but not locally closed out | `status=review_approved`; local `next` still asks owner finalize | `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-004` |
| Parent PR is merged | PR #2096 state `MERGED`; merge commit `9cb0158f4f8902be620ecd4326a4884754e92c21`; merged at `2026-06-21T15:39:52Z` | `gh pr view 2096 --repo ajoe734/pantheon --json ...` |
| Visible GitHub checks passed | Commit trailers, Runtime mirror guard, Smoke acceptance, Forward to orchestrator all `pass` | `gh pr checks 2096 --repo ajoe734/pantheon` |
| Merged parent file surface | 5 files: parent task brief plus `SPEC.md`, `__init__.py`, `skill.py`, `test_skill.py` under result_synthesis | `git show --stat --oneline 9cb0158f4f8902be620ecd4326a4884754e92c21` |

Timing note: `ai-status.json` still shows `AG-BE-RS-004` as
`review_approved` with a stale `next` note about PR #2096 being behind. GitHub
metadata now shows the PR merged. This is a parent closeout/state-sync matter,
not a sidecar implementation finding.

### 3.2 Local validation run by this sidecar

Commands run from `task/AG-BE-RS-004-SIDECAR-REVIEW` after fast-forwarding the
branch to current `origin/dev`:

| Command | Result |
|---|---|
| `python3 -m pytest integrations/openclaw/skills/agora/result_synthesis/test_skill.py -v` | `28 passed in 5.54s` |
| `python3 -m pytest services/control-plane/tests/agora/test_winner_branch_e2e_v13.py::TestStep7ResultsAndPatchProposal -v` | `11 passed in 1.52s` |
| `python3 -m pytest services/control-plane/tests/agora/test_winner_branch_e2e_v13.py --collect-only -q` | `89 tests collected`; Step 7 class contains the 11 VersionPatchProposal / patch proposal tests |

Exploratory selector note: `python3 -m pytest
services/control-plane/tests/agora/test_winner_branch_e2e_v13.py -k
VersionPatchProposal -v` selected 0 tests and exited with pytest code 5
because the intended tests are named under
`TestStep7ResultsAndPatchProposal`. The direct class command above is the
validation evidence.

### 3.3 Implementation evidence

| Acceptance point | Evidence |
|---|---|
| v1.3 patch schema is loaded from canonical schema path | `integrations/openclaw/skills/agora/result_synthesis/skill.py:73` computes `_VPP_SCHEMA_PATH` as `services/control-plane/specs/agora/v4/version_patch_proposal.schema.json`; `_validate_proposed_patches()` uses `jsonschema.Draft7Validator` at `skill.py:89` and `skill.py:109`. |
| Input grounding is mandatory | `run_result_synthesis()` blocks empty `research_run_refs` or input `evidence_refs` with `INPUT_SCHEMA_INVALID` at `skill.py:246`. |
| Degraded mode does not forge a verdict | `synthesis_adapter is None` returns `status=blocked`, `SYNTHESIS_ADAPTER_UNAVAILABLE`, and warns that no verdict/evidence is fabricated at `skill.py:259`. |
| Adapter privacy boundary is ref-only | Adapter call receives strategy spec ref, base version id, research run refs, consult memo refs, evidence refs, and optional decision style ref at `skill.py:290`; no raw prompt, user identity, or journal content is passed. |
| Stub/fixture proof cannot be promoted as production proof | Non-production modes emit `STUB_RESULT_NOT_PRODUCTION_PROOF`; stub-only `promising` is downgraded to `needs_revision` at `skill.py:314`. |
| Output evidence refs are scoped to input evidence | `_filter_evidence_scope()` filters output refs to caller input; invented refs emit `INVENTED_EVIDENCE_REF` at `skill.py:327`. |
| Ungrounded non-insufficient conclusions are blocked | `promising`, `needs_revision`, and `reject` with empty scope-filtered evidence refs return blocked `INSUFFICIENT_EVIDENCE` at `skill.py:339`. |
| VersionPatchProposal schema violations block output | Proposed patches are validated and any error returns blocked `PATCH_SCHEMA_INVALID` at `skill.py:352`. |
| Verdict enum drift is contained | Unknown verdicts are downgraded to `insufficient` at `skill.py:361`. |
| Output is proposal only | Returned model only includes synthesis output fields; no RuntimeBinding, capital binding, broker order, governance execution, or live-enable side effect is present in `run_result_synthesis()`. |

### 3.4 Test evidence

| Test cluster | Evidence |
|---|---|
| Golden evals | `test_skill.py:1` documents the three C1 golden evals; tests cover V3/V4 threshold liquidity, OOS failure despite IS pass, and consult disagreement preservation. |
| Evidence scope enforcement | `test_skill.py:545` through `test_skill.py:601` covers invented refs being filtered, all-invented refs blocking non-insufficient verdicts, and insufficient verdicts allowing empty filtered refs. |
| VersionPatchProposal validation | `test_skill.py:608` through `test_skill.py:683` covers invalid schema, additional unknown field rejection, valid schema forwarding, and no-patch skip behavior. |
| Conflict preservation and patch forwarding | `test_skill.py:690` and `test_skill.py:711` verify unresolved decisions pass through verbatim and valid proposed patches are forwarded. |
| v1.3 winner-branch Step 7 | `services/control-plane/tests/agora/test_winner_branch_e2e_v13.py:623` defines `TestStep7ResultsAndPatchProposal`; the 11 passing tests cover accepted/draft proposal schema, RFC 6902 restricted patch format, allowed operations/paths, base hash validation, immutable registry draft behavior, proposal id pattern, and patch SSE events. |

## 4. Residual Notes For Parent Owner

No sidecar-blocking implementation finding emerged in this pass.

Parent-owned closeout notes:

- `AG-BE-RS-004` still appears as `review_approved` in local `ai-status` even
  though GitHub PR #2096 is merged. The parent owner should run the normal
  closeout finalization flow when ready.
- `ai-status` lists the parent artifact as
  `integrations/openclaw/skills/agora/result-synthesis/`, while the merged
  implementation path is `integrations/openclaw/skills/agora/result_synthesis/`.
  If status artifact precision matters during parent closeout, the parent owner
  should reconcile that metadata in the parent flow.
- Parent PR #2096 merged with visible checks green. This packet does not run
  the full 89-test winner-branch suite or any broader Agora/BFF regression
  suite; it only runs the focused result-synthesis and Step 7 patch proposal
  validations listed above.
- The implementation SPEC intentionally expands the brief C1 design-closure
  SPEC with task-specific enforcement details and failure codes. This sidecar
  treats that as implementation support documentation, not a promotion of new
  canonical truth.

## 5. Support-Only Boundary Confirmation

- No L1 canonical policy or architecture document was edited by this sidecar.
- No OpenAPI bundle, JSON schema, capability manifest, runtime registry, BFF
  router, adapter, governance implementation, or parent implementation file was
  edited by this sidecar.
- No parent task status was changed by this sidecar packet.
- No new execution task was materialized by this sidecar.
- The intended sidecar artifact is this file:
  `support/sidecars/AG-BE-RS-004/AG-BE-RS-004-SIDECAR-REVIEW.md`.

## 6. Handoff Recommendation

Reviewer: `Claude`

Recommended review checks:

1. Confirm this packet accurately captures PR #2096's merged state, merge
   commit, file surface, and visible green checks.
2. Confirm the focused validations are sufficient for a support-only review
   packet and that any broader parent verification remains parent-owned.
3. Confirm sections 3.3 and 3.4 fairly summarize the implementation guardrails
   without changing canonical truth.
4. If accepted, approve `AG-BE-RS-004-SIDECAR-REVIEW`; parent lifecycle closeout
   remains separate under `AG-BE-RS-004`.

Suggested reviewer command after approval:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve AG-BE-RS-004-SIDECAR-REVIEW "Review packet approved; parent PR #2096 merge/check evidence and focused result-synthesis validation captured as support-only handoff."
```

Suggested reviewer command if changes are required:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen AG-BE-RS-004-SIDECAR-REVIEW "Describe the specific correction needed in the sidecar packet."
```

Prepared by Codex for the `AG-BE-RS-004-SIDECAR-REVIEW` support slice.
