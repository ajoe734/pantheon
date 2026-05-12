# QLIB-ACT-002-SIDECAR-ACCEPTANCE

This document serves as the acceptance packet for the sidecar support slice related to the QLIB-ACT-002 parent task.

**Helper Kind:** acceptance_packet
**Parent Task:** QLIB-ACT-002
**Task ID:** QLIB-ACT-002-SIDECAR-ACCEPTANCE

## Sidecar Description

This sidecar is a support component designed to facilitate the acceptance process for the QLIB-ACT-002 task. It is not part of the canonical implementation but provides necessary artifacts and documentation for review and handoff.

## Acceptance Criteria

*   [ ] The sidecar's functionality aligns with its stated purpose as a support artifact.
*   [ ] All required supporting documentation (e.g., READMEs, configuration) is present and accurate within the designated support directories.
*   [ ] The handoff packet is complete and includes all necessary information for the parent owner's review.
*   [ ] The sidecar's code and artifacts adhere to project conventions for support slices.

## Current Status

*   **Initial Packet Creation:** The core acceptance packet file has been created. Further details will be added as the sidecar development and documentation progresses.

## Next Steps

1.  Gather/prepare the TW OHLCV dataset (≥50 instruments, ≥2 years, ≥504 daily periods).
2.  Construct the production dataset proof JSON, ensuring all required fields are populated.
3.  Execute `services/research/qlib/production_activation_smoke.py --backend real` with the dataset and proof.
4.  Verify the generated activation packet's state (`artifact_state=draft`, `deployment_summary.current_stage=none`).
5.  Ensure the packet correctly references the StrategySpec ID (`qlib-tw-cross-sectional-alpha-spec-v1`).
6.  Document the process and findings, referencing evidence in relevant files (e.g., `support/qlib-activation/dataset-build-log.md`, `integrations/qlib/activation_packet.md`).
7.  Submit the results for review.
the QLIB-ACT-002 sidecar.
2.  Gather and link all relevant support artifacts (code, configuration, READMEs).
3.  Finalize the handoff packet.
4.  Submit for review by the parent task owner.
