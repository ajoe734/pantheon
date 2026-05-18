# Bundle Verification Probe

This probe verifies that the production bundle is free of mock and seed-fallback assets.

## Probe Mechanism

1. The probe crawls the deployed bundle.
2. It checks for network requests or bundle references matching:
   - `/mocks/`
   - `seed.*`
3. Any match indicates a violation of the strict publish policy.

## Result interpretation

- PASS: No mock or seed-fallback references found.
- FAIL: Mock or seed-fallback references detected.
