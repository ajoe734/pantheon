# V6 — Dependency pin hygiene (direction D/E, broadening)

- Date: 2026-06-14
- Branch: task/verify-v6-req-consistency
- Non-duplication: no brief covers requirements/pin consistency or hygiene (rllib/oss
  briefs are about ML implementation, not dependency hygiene). Distinct from V2 (CVEs).

## Verification & findings (`scripts/audit_dependency_hygiene.py`)
Scanned 48 requirements files / 176 dependency lines:
- **158 of 176 dep lines (90%) are UNPINNED** (no `==`) across 42 files (worst:
  control-plane/bff 11, learning/trl 7, consultation 6, persona 6). Unpinned deps make
  builds non-reproducible and are exactly how uncontrolled CVE exposure (V2) drifts in.
- 1 cross-file conflicting exact pin: `gymnasium 0.28.1 (rllib) vs 1.2.3 (finrl)` —
  BENIGN: these are separate isolated images, not a shared env.

## Fix / disposition
- Delivered the reusable audit (conflicts + unpinned ratio; `--max-unpinned-ratio`
  can gate once a target is set).
- NOT auto-pinning 158 deps this round: each needs resolution to a tested version +
  an image rebuild; a blind sweep would risk breaking many services. Tracked as a
  dedicated hardening effort (recommend `pip freeze`/lockfiles per service, rolled out
  with rebuild verification). This finding is the round's value: a systemic, previously
  unmeasured reproducibility/security gap is now quantified and gate-able.

## Follow-ups
- Pin service deps (lockfiles) starting with the live-path services (bff, runtime-manager,
  telemetry, broker, governance), each with a build/verify before deploy.
- Wire the hygiene audit into CI with a ratcheting `--max-unpinned-ratio`.
