# MGMT-OPS-003 Hosted Gap Archive - 2026-07-11

Status: active gap source of truth

Owner: Codex

This archive records the difference between the accepted MGMT-OPS-003 plan and
the behavior observed on the Pantheon-owned dev frontend after the BFF contract
was deployed. It does not reopen or duplicate the completed BFF slice. It
defines the remaining frontend, data-quality, workflow, and reviewer work.

## Documents

- `MGMT_OPS_003_HOSTED_GAP.md` - hosted evidence, plan-to-live difference
  matrix, delivery boundaries, and completion definition.
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/INDEX.md` - fleet
  execution packet.
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/REVIEWER_CHECKLIST.md`
  - fail-closed reviewer contract.

## Delivery Boundary

- Frontend implementation belongs to `ajoe734/execute-plans` on merge target
  `main`.
- BFF and runtime data-quality implementation belongs to
  `ajoe734/pantheon` on merge target `dev`.
- Frontend source must never be copied into a Pantheon checkout.
- Hosted dev acceptance uses:
  - `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
  - `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

## Dispatch

Use the merged dispatcher:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/dispatch_mgmt_ops_003_hosted_gap_2026-07-11.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon \
  python3 scripts/ai_status.py sync
```

The dispatcher does not assign Qwen and preserves existing task progress.
