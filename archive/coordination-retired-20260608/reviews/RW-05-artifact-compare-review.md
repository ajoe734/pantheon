# RW-05 Artifact Compare Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Findings

### 1. High: the republished RW-05 completion packet is still local-only and not Git-visible

- The Pantheon-owned contract gap is fixed, but the returned front publication
  is not yet replay-clean from Git-visible history:
  - `origin/pkt-004-detail-fix` currently resolves to
    `1a1a42eebda033a1fbda4696df5b81271f5eed9b`
  - local branch `pkt-004-detail-fix` is ahead at
    `5b09232065198532b5b2f466ebd61c8169207bd0`
- The remote branch does contain the reviewed RW-05 UI source commit
  `6321613cff3c49b11a7619e0f9170217a27a7b17`, but it does not contain the
  republished coordination return:
  - `git -C ../front-ai-trading-system branch -r --contains 6321613cff3c49b11a7619e0f9170217a27a7b17`
    includes `origin/pkt-004-detail-fix`
  - `git -C ../front-ai-trading-system show origin/pkt-004-detail-fix:.coordination/requests/RW-05-artifact-compare-ui-done.yaml`
    fails because the file is still local-only
- The local-only delta from `origin/pkt-004-detail-fix` to
  `pkt-004-detail-fix` adds:
  - `.coordination/requests/RW-05-artifact-compare-ui-done.yaml`
  - `.coordination/requests/RW-05-artifact-compare-frontend-feedback.yaml`
  - `docs/pantheon-feedback/RW-05-artifact-compare/*`

Impact:

- Pantheon can confirm that the RW-05 contract and reviewed UI are now aligned,
  but it cannot honestly mark the loop replay-clean or supervisor-complete
  through Git-visible coordination artifacts until that republish lands on the
  returned branch.

## Verified Positives

- Pantheon's 2026-04-24 RW-05 list-authority fix is real in the current
  workspace:
  - `GET /api/v1/artifacts` now publishes list-level
    `artifacts[].allowedActions.canCompare`
  - `python3 -m pytest -q services/control-plane/bff/test_rw05_artifact_compare_contract.py`
    passed with `6` tests
- The reviewed UI source commit
  `6321613cff3c49b11a7619e0f9170217a27a7b17` still matches the refreshed
  Pantheon contract:
  - `ArtifactRegistry.tsx` forwards the published filter params and gates
    selection only from list-level `allowedActions.canCompare`
  - `ArtifactDetail.tsx` renders `version_chain`, provenance, metrics,
    parameters, and `resolved_link` directly from the detail payload
  - `ArtifactCompare.tsx` submits only selected `artifact_ids` and renders
    backend-composed `field_pairs`, `change_summary`, and `provenance_pairs`
    without browser-side diff logic
  - `src/lib/bffClient.ts` keeps RW-05 on the shared `artifactApi`
- The local republish did not change the reviewed RW-05 UI snapshot:
  - `git diff --name-only
    6321613cff3c49b11a7619e0f9170217a27a7b17
    5b09232065198532b5b2f466ebd61c8169207bd0 --` over the RW-05 UI source file
    set is empty
- Front static verification passed in the sibling front workspace on
  2026-04-24:
  - targeted `npx eslint` over the RW-05 files
  - `npx tsc --noEmit`
  - `npm run build`
  - build emitted only the existing Browserslist age notice and Vite
    chunk-size warning

## Decision

`RW-05-artifact-compare` is **follow-up required**.

No new Pantheon BFF or contract work is required for this cycle. The only
remaining blocker is transport publication:

1. front must publish local republish commit
   `5b09232065198532b5b2f466ebd61c8169207bd0` or an equivalent truthful
   replacement to Git-visible history on `pkt-004-detail-fix`
2. the published branch must then contain:
   - `.coordination/requests/RW-05-artifact-compare-ui-done.yaml`
   - `.coordination/requests/RW-05-artifact-compare-frontend-feedback.yaml`
   - `docs/pantheon-feedback/RW-05-artifact-compare/*`
3. both request files must continue to point at reviewed UI source commit
   `6321613cff3c49b11a7619e0f9170217a27a7b17` unless a new reviewed UI
   snapshot is intentionally created

After that Git-visible republish lands, Pantheon can re-run the transport check
and close RW-05 without reopening the resolved contract work.
