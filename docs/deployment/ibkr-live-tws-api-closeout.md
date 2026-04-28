# IBKR Live TWS API Closeout

Status: read-only broker-path validation complete

## Verdict

`IBKR` is now validated through the live `TWS API` lane for the current APP-003
wave at the broker-session level.

The verified path is:

- operator live login on VM2 `TWS`
- `TWS API` enabled on `7496`
- Pantheon VM1 cross-VM connection to `10.140.0.5:7496`
- broker-authenticated read-only probe returning account ownership and summary

## Evidence

Primary packet:

- [README.md](/home/edna/code/pantheon/docs/deployment/evidence/ibkr-live-tws-api/20260425T160338Z/README.md)
- [probe-summary.json](/home/edna/code/pantheon/docs/deployment/evidence/ibkr-live-tws-api/20260425T160338Z/probe-summary.json)

Probe result highlights:

- `status = ok`
- `nextValidId = 1`
- `managedAccounts = ["U19859952"]`
- `account_ref_present = true`
- account summary returned for `U19859952`

## Boundaries

This closeout proves:

- live broker session reachability
- correct `TWS API` lane, not the earlier `FIX/CTCI` lane
- cross-VM Pantheon connectivity
- read-only broker truth retrieval

This closeout does not prove:

- live order submission
- cancel lifecycle
- fill handling
- post-trade telemetry for a real order

## Next Step

If the operator wants to continue beyond read-only verification, use the manual
runbook at:

- [ibkr-minimal-live-order-cancel-manual.md](/home/edna/code/pantheon/docs/deployment/ibkr-minimal-live-order-cancel-manual.md)

That path must remain manually supervised and should not be represented as
already executed until a real evidence packet exists.
