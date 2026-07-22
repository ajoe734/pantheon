# LOOP-AUTO-BFF-003 Evidence

Task: `LOOP-AUTO-BFF-003`
Owner: Codex
Reviewer: Claude
Date: 2026-06-27

## Delivered Surface

- Added operator-facing truth labels to BFF loop health packets:
  - `Seed / fixture`
  - `Snapshot fallback`
  - `Registry metadata`
  - `Scheduled tick`
  - `Reconciled live truth`
  - `Proven live truth`
- Added `evidence_packet.operator_truth` and `live_status.operator_truth`.
- Added `managementClient.loopHealth` against `GET /bff/v5/loop-health`.
- Added the Management `LoopTruthPanel` first-screen operator panel.

## Truth Boundary

Seed, fixture, local snapshot fallback, registry metadata, and scheduled tick
truth are visible but are not accepted as live liveness proof. Local snapshot
fallback can preserve the highest available raw health value for audit, but the
operator-facing `operator_truth` label remains `Snapshot fallback` and
`accepted_as_live: false`.

## Verification

```bash
python3 -m pytest services/control-plane/bff/test_loop_health_read_model_contract.py services/control-plane/bff/test_loop_inventory_read_model_contract.py
```

Result: `9 passed`.

```bash
npm test -- --run src/management/components/loop-truth/LoopTruthPanel.test.tsx
```

Result: `1 passed`.

```bash
npm test -- --run src/lib/bff/__tests__/client.test.ts -t loopHealth
```

Result: `1 passed`, `28 skipped`.

```bash
npm run build:management
```

Result: passed.

Attempted broader frontend command:

```bash
npm test -- --run src/management/components/loop-truth/LoopTruthPanel.test.tsx src/lib/bff/__tests__/client.test.ts
```

Result: failed in existing `client.test.ts` baseline cases unrelated to this
task, including `liveStatus._reset is not a function` and mock seed list
expectations. The new loop truth panel test initially exposed missing
jest-dom matchers and was repaired to use Vitest/Chai assertions.
