# AG-UIPOL-009 hosted evidence

Captured: 2026-07-13 22:57:00 UTC

This record proves the V10 Strategy Workshop expert dialogue design parity and Playwright e2e test execution for `AG-UIPOL-009` on the hosted dev environment.

## Delivered revisions

- execute-plans PR [#317](https://github.com/ajoe734/execute-plans/pull/317) (target branch `dev`) introduced the Strategy Workshop parity:
  - SW-01: Add NewWorkshopForm for entering a new strategy description and examples.
  - SW-02: Support strategy_core, research_subproblems, recognized_components, and non_assertable_claims in ServantReconstruction card.
  - SW-03: Render prioritized missing/conflicting assumptions and one high-information question in NextQuestionPanel.
  - SW-04, SW-0SW-06: Expose methodology, sample, confidence, caveats, conclusions, and specific Winner Branch research details in ResearchResult card.
  - SW-07: Implement 12-block completeness map in StrategyCompletenessRail, fallback gracefully to parent dimension grades, and keep compatibility wrappers.
- The exact commit of the implementation is `e60fd27a635ae453ec964985fd499d588c3750f4`.
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` reported app `execute-plans`, source branch `dev`, live/strict BFF mode before capture.

## Strategy Workshop proof

The browser loaded the Strategy Workshop hosted route without response interception:
- `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/strategy-workshop`

Assertions:
- SW-01: The blank/new strategy intake form is visible with professional strategy description input, example presets, and a "Start Discussion" button.
- SW-02 to SW-06: The expert strategy cards (reconstruction, next question, missing/conflicting assumptions, research results with confidence/caveat/provenance, sizing discussion) render correctly with the correct styling and no raw UUID leakages.
- SW-07: The V10 12-block completeness rail maps dimension completeness correctly and falls back gracefully to parent dimension grades.

Screenshots:
- [desktop strategy workshop proof](./AG-UIPOL-009-desktop.png)
- [mobile strategy workshop proof](./AG-UIPOL-009-mobile.png)

## Machine-readable readbacks

- [AG-UIPOL-009-desktop.json](./AG-UIPOL-009-desktop.json)
- [AG-UIPOL-009-mobile.json](./AG-UIPOL-009-mobile.json)

Artifact SHA-256:

- `8d656f5fefeb98125898859c3b992f41a46c797805c3b4cb1e79712880faea62` — AG-UIPOL-009-desktop.json
- `a741efc92ec8bd8761dfbf3d88687fd45e827889b6a0b662360f4e029f7398c8` — AG-UIPOL-009-desktop.png
- `b14bc1e313e5855ce20d3175a896035d73079fcd59de90ab86f0d8354021470c` — AG-UIPOL-009-mobile.json
- `3ba1c0da4d26118eee986760953ab9535ebd5324e281bcbf5d14f01a681756b5` — AG-UIPOL-009-mobile.png

## Validation and residuals

- `npm run test` in `/home/lupin/code/execute-plans` passes successfully (1348/1348 tests).
- Playwright E2E verification command:
  ```bash
  BFF_AUTH_TOKEN="lupin:operator,reviewer,approver,risk_owner,admin:mfa:assistant.kernel.debug,assistant.kernel.repair" \
  PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
  npx playwright test e2e/agora-strategy-workshop-hosted.spec.ts
  ```
  Status: 4/4 passed.
