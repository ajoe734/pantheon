# Verified shared quota health

Task: `OPS-SUPERVISOR-SHARED-QUOTA-HEALTH-GROUP-CORRECTIVE-001`.

The operator verified that Claude and Claude2 consumed the same exhausted
session after direct 429 evidence. Their provider entries now reference the
same existing opaque `account` value. This is an explicitly authorized
relationship, not an inference from agent names. Do not publish account labels,
credential material, or credential paths in diagnostics or review evidence.

`providers.*.account` remains the only quota-health and concurrency identity.
The shared account retains its existing limit of three concurrent workers.
Claude retains its lane limit of three and Claude2 its lane limit of one;
their aggregate use cannot exceed the shared account limit. All account-limit
entries, including the now-unused former secondary account limit, are retained.
Provider keys, agent names, adapter settings, credentials, worker slots and
fallback maps are unchanged. Healthy Claude/Claude2 remain independent reviewer
identities. Auth failure stays endpoint-local.

The existing worker failure observer writes terminal quota into
`delivery_health.accounts`. Admission therefore rejects both lanes. Retry/reset
expiry requests a fresh probe; elapsed time or cached success does not establish
health. A new valid live observation restores the account according to the
existing provider-health semantics.

The configured fallback graph still controls recovery. An exhausted sibling is
ineligible. An unrelated eligible lane with fresh health and spare capacity may
be selected through the existing canonical reassignment CAS. Otherwise the
existing pending recovery receipt fences dispatch. Repeated reconciliation
reserves one replacement intent; only the ordinary planner/launcher can launch
the replacement. No scheduler, parallel retry mechanism or health store is added.

## Validation

Run the isolated source regression:

```bash
timeout 120 python3 -m unittest discover -s .orchestrator -p test_shared_quota_config.py -v
```

It loads the real configuration and covers schema/identity, shared active counts
and capacity, quota failure from either lane with explicit/default retry,
fresh-probe recovery, endpoint-local auth, healthy/full fallback selection,
duplicate lease admission, and authoritative temporary TaskStore recovery.
All health observations are synthetic; no provider is contacted or worker
launched. The existing supervisor config contract test was updated because its
former assertion explicitly required the now-obsolete split account relation.
Exact commands and exits are in the task evidence manifest.

## Promotion and rollback acceptance

Source delivery is not live activation. The pre-review live readback still had
separate account identities. Independent exact-head review, required CI and the
supervisor integration merge must precede promotion.

After merge, request the existing supervisor promotion operator to provision
the sealed command runtime for the exact merged SHA and run
`scripts/promote-supervisor-runtime.sh` with its existing host bindings. First
use discovery/preflight, then its governed `--promote` operation. Follow
[the promotion drain runbook](supervisor-runtime-promotion-drain.md); do not copy
configuration into the incumbent runtime or restart it manually.

Record the terminal promotion exit, accepted source/runtime SHAs, watchdog
health and redacted group equality. Verify both provider keys and agent lanes
remain distinct, all capacity limits are preserved, and the rendered account
relation is shared. Existing topology reconciliation prunes orphan account
health; fresh probes remain the authority for current health. Do not copy or
invent healthy evidence for the changed topology.

Read canonical recovery receipts and queue/lease identities through existing
read-only diagnostics. For a naturally observed terminal-quota recovery, verify
the sibling receives no replacement, each failed lease has one canonical
receipt, and at most one replacement queue intent/worker matches that receipt
and task generation. If no suitable live scenario occurs, record that live
proof as pending; the isolated test is not a live incident. Never inject quota
failure into live state to manufacture acceptance evidence.

Promotion failure uses the promoter's existing rollback and records terminal
rollback health plus the restored exact runtime identity. For a later semantic
regression, prepare a scoped source revert and promote its reviewed merged SHA
through the same flow. Restoring the former split topology also restores its
known shared-session reassignment risk; operator review must assess that
tradeoff. Keep failed attempts fenced under existing authority. No hand edits
to canonical tasks, queue JSON, health, credentials or leases are part of this
procedure. A planned rollback is not an executed rollback result.

Freeze this manifest before review. Record subsequent merge/promotion evidence
through the governed closeout checkpoint and promotion evidence output; do not
move the approved PR head merely to add post-review bookkeeping.
