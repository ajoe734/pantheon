# Approved recommended measure consumer evidence

Task: RECOMMENDED-MEASURE-CONSUMERS-20261112  
Owner: Codex2; independent reviewer: Claude  
Base: caec3f40727c6bf7dd73c710625deff81b5fd4eb

The exact persisted recommendation candidate now forwards approved requests to
existing Research discovery tickets, Memory lesson candidates, and Persona
harness draft proposals. Domain review and execution stay with those owners.
The BFF adapter creates no new domain state machine or runtime authority.
Unsupported types and legacy cards lacking measure provenance are refused.

The HTTP integration suite exercises candidate creation, accept-for-review,
authoritative validation, formal approval linkage, receipt readback and retries.
It uses real JWT extraction, role checks, tenant/user scope resolution, candidate
service, binding validator, canonical approval read adapter and stores. Upstream
persisted opinion/invocation and canonical approval readback are seeded fixtures;
consumer owners are substituted only at their HTTP boundary. The HTTP fixture
validates Memory's actual JSON schema, Persona's actual PersonaPatch model and
Research's actual ticket projection, rather than echoing a made-up receipt.
PostgreSQL tests replace/reopen the store and recover a successful remote write
whose response was lost, verifying one downstream record and one local receipt.

`focused-tests.txt` contains the final bounded test command's terminal output.
`evidence.json` maps each acceptance criterion and records verification status.
No hosted run or deployment is claimed. Existing upstream canonical approval
readback must be available and return the strict checksummed receipt
contract; its absence continues to fail closed. This change does not introduce
an approval issuer or broaden that contract.

## Review boundaries

- Stable owner IDs derive from the scoped persisted measure, never an HTTP
  attempt's idempotency key. Changed request bytes under that identity conflict.
- A failed owner call leaves approval and consumer receipt uncommitted. Retry
  the same approval link; recovery reads and checks the deterministic owner ID.
  Recovery is request-driven, not a new background dispatcher or scheduler.
- Receipts live in the existing durable candidate idempotency store and appear
  in candidate readback as `downstream_receipts`. Canonical approval receipts
  retain their original checksum and bytes.
- Research requests create open tickets owned by the recommending Persona.
- Memory requests require the measure's actual reflection provenance and create
  proposed lesson candidates. They do not endorse, merge or write memory entries.
- Harness requests bind the recommending Persona and exact base version, and
  create draft patches. The existing configured Persona service principal may
  authorize that draft API, which already requires persona.admin. No credential
  is generated or changed. No precheck, activation or policy write is invoked.
- Accepted measures become immutable through both service and store checks.
- The existing PostgreSQL candidate/outbox regression expected a timeline without
  the now-canonical queued event. Its assertion now selects the candidate-created
  event, preserving the intended atomic-link proof while retaining queued history.

The upstream Memory task's natural hosted event, retrieval/retry and cross-Persona
negative proofs remain outside this source delivery and are not marked passed.
