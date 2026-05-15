# IBKR Live TWS API Evidence

Timestamp: `2026-04-25T16:03:38Z`

Scope: read-only proof that Pantheon VM1 can reach the live `TWS API` session
running on VM2 and recover broker-authenticated account signals without placing
orders.

## Result

- `status`: `ok`
- VM1 -> VM2 target: `10.140.0.5:7496`
- account ref: `U19859952`
- `nextValidId`: `1`
- `managedAccounts`: contains `U19859952`
- `account_summary`: returned for `U19859952`

## What This Proves

1. `TWS live` login completed on VM2.
2. `Enable ActiveX and Socket EClients` is effective on the live TWS session.
3. Pantheon VM1 can connect to the VM2 `TWS API` socket over the internal VPC.
4. The session is broker-authenticated because `nextValidId` and
   `managedAccounts` resolved successfully.

## What This Does Not Prove

- no live order was submitted
- no cancel/fill lifecycle was exercised
- the API remained in `Read-Only` mode during this probe

## Evidence Files

- `probe-summary.json`

## Notes

The probe intentionally stayed read-only. The returned `error_code=321` is
expected while `Read-Only API` remains enabled in TWS.
