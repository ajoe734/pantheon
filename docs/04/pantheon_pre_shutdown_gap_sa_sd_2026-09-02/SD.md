# System Design — Pre-shutdown Gap Closure

Status: implementation-ready design

Baseline: `origin/dev` at `4889e498fbe5c3b87e7a66b3ca19897e030bbcc1`

Companion documents: [GAP_REPORT.md](GAP_REPORT.md), [SA.md](SA.md)

## 1. Delivery strategy

Use one clean task branch per residual gap. Do not combine the high-churn BFF
cleanup with ingestion, bootstrap, or development-tooling recovery. Each branch
must rebase on current `origin/dev`, retain only its owned files, run the
specified validation, and carry exact-head review evidence before merge.

Recommended delivery packets:

| Packet | Source line | Owned layer | Depends on |
|---|---|---|---|
| PSD-05 | runtime reconstruction | development tooling | none; first operational prerequisite |
| PSD-03 | egress/tunnel retirement | ingestion policy and dashboard publication | current `dev` |
| PSD-04 | telemetry replay auth | bootstrap orchestration | current `dev` |
| PSD-02 | WAL/SHM recovery | distillation queue | current `dev` |
| PSD-01 | BFF composition cleanup | BFF assembly and route ownership | all merged router waves |
| PSD-06 | integrated hosted acceptance | evidence only | PSD-01 through PSD-05 |

## 2. PSD-01 — BFF composition-root completion

### 2.1 Owned files

- `services/control-plane/bff/main.py`
- affected domain routers and services only where mounting/parity requires it
- final deletion of `services/control-plane/bff/read_store.py`
- BFF composition, route, architecture, smoke, and affected-domain tests

### 2.2 Implementation sequence

1. Rebase the V3 branch on current `dev` and resolve router ownership using the
   merged domain router as authoritative.
2. Generate a normalized route inventory from the baseline and candidate.
3. Reduce `main.py` to app construction, middleware, exception handlers,
   dependency wiring, and router mounting.
4. Move no domain business behavior back into `main.py`.
5. Delete orphaned legacy handler/helper bodies.
6. Delete `read_store.py` only after production caller search is empty.
7. Mount Pack D exception handlers through `_build_bff_app`.
8. Add the governance rollback-review route through its domain router.
9. Compare route method/path/auth semantics and document every intentional
   delta.

### 2.3 Required guards

- AST check: no inline production `@app.<method>` decorators in `main.py`.
- Import/caller check: no production caller of `read_store`.
- Route check: no duplicate normalized method/path pair.
- Resolution check: no static route shadowed by a parameter route.
- Symbol check: no undefined call symbols from composition.
- Contract check: response envelope and authorization remain stable.

### 2.4 Validation

Run at minimum:

```bash
pytest -q \
  services/control-plane/bff/tests/test_bff_main_composition.py \
  services/control-plane/bff/test_architecture_boundaries.py \
  services/control-plane/bff/tests/test_read_store_final_deletion.py \
  services/control-plane/bff/test_normalized_route_uniqueness.py \
  services/control-plane/bff/test_route_resolution_no_shadowing.py \
  services/control-plane/bff/test_no_undefined_call_symbols.py \
  services/control-plane/bff/smoke_test.py \
  services/control-plane/bff/smoke_test_incident.py
```

Add affected Agora, Research, Management, Persona, Governance, Events, and
Training router suites selected by the final diff.

## 3. PSD-02 — SQLite database-family quarantine

### 3.1 Recovery algorithm

Given queue path `Q`, derive the ordered family:

```text
Q
Q-wal
Q-shm
```

On confirmed corruption:

1. close all connections to `Q`;
2. allocate one recovery timestamp/identifier;
3. record which family members exist;
4. rename every existing member into the quarantine namespace using the same
   identifier;
5. fsync the containing directory where supported;
6. open a fresh queue at `Q` and initialize schema;
7. record a sanitized recovery receipt; and
8. resume only after a write/read probe succeeds.

Do not copy sidecars back, reuse them, or report recovery before all existing
members have moved. The implementation must preserve the current concurrency
lock around queue recovery.

### 3.2 Tests

- main file only;
- main plus WAL;
- main plus WAL and SHM;
- missing optional sidecars;
- rename failure after discovery;
- new connection does not attach quarantined WAL;
- repeated recovery produces a new identifier without overwriting evidence;
- restart accepts work and preserves new queue records.

## 4. PSD-03 — egress and tunnel retirement

### 4.1 Connector state

Set these connector definitions to `DISABLED_BY_BUILD`:

```text
tw-yahoo-broker-top15
tw-yahoo-stock-rss
tw-anue-news-rss
```

Remove their `SourceUpdateRule` entries from the active universe. Remove the
Yahoo broker fallback from the FinMind quota-exhaustion path. Policy summaries
must not advertise any of the three as active or fallback connectors.

Do not change official/licensed connector cadence or support state as part of
this packet.

### 4.2 Tunnel default

`PANTHEON_DASHBOARD_MANAGE_TUNNEL` defaults to disabled. Remove standing
permission-broker grants for `cloudflared tunnel` and
`scripts/start_dashboard_tunnel.sh`. Explicit Human/Ops invocation remains
possible through the normal approval path.

### 4.3 Tests

- default active universe contains none of the disabled connectors;
- quota exhaustion does not select Yahoo;
- policy summary omits the Anue/Yahoo schedule;
- supported-source set remains unchanged for approved sources;
- dashboard autostart without an opt-in never launches `cloudflared`;
- worker permission resolution denies unattended tunnel launch.

## 5. PSD-04 — telemetry replay authorization

### 5.1 Inputs

```text
PANTHEON_TELEMETRY_OPERATOR_TOKEN  optional secret
PANTHEON_TELEMETRY_SERVICE_TENANTS preferred tenant source
PANTHEON_TENANT_ID                 secondary tenant source
default                            final tenant fallback
```

### 5.2 Control flow

```text
--skip-telemetry-replay supplied -> print skip reason -> Step 5
no operator token               -> print best-effort skip -> Step 5
operator token supplied         -> POST replay with Bearer + X-Tenant-Id
  2xx                            -> print sanitized result -> Step 5
  non-2xx                        -> print sanitized status/error -> exit nonzero
```

Forward the secret into the telemetry container with a bounded environment
variable. Never echo it, interpolate it into trace output, store it in an
evidence file, or substitute the telemetry service token.

### 5.3 Tests

- shell syntax and embedded Python parse;
- no-token path reaches Step 5 with exit zero;
- explicit skip reaches Step 5;
- valid operator token sends both headers and succeeds;
- invalid/rejected token exits nonzero;
- tenant precedence matches the contract;
- captured stdout/stderr contains no token value.

## 6. PSD-05 — host-independent development runtime

### 6.1 Configuration

Provider homes use `~` or validated environment-derived paths. Deployment
layout is selected by `PANTHEON_DEPLOY_ROOT`, retaining the established path as
a backward-compatible default. The runtime config must still resolve to
absolute paths before process launch.

### 6.2 Bootstrap phases

The runtime bootstrap must be idempotent and expose `--dry-run`:

1. validate repository identity and clean source commit;
2. validate bubblewrap/user-namespace support before sealing a runtime;
3. create deployment directories with bounded permissions;
4. create a local dev-bridge Ed25519 keypair only if absent;
5. create a detached command-root worktree for the exact source SHA;
6. promote and seal the command runtime;
7. install/update watchdog ownership;
8. validate supervisor identity against command root and live config; and
9. report exact runtime SHA and paths without secrets.

### 6.3 Empty-journal recovery

`seed_task_state_genesis.py` may append a genesis event only when the target
journal contains zero valid events. It requires an explicit source description
and emits a checkpoint. If any event or ambiguous partial content exists, it
refuses mutation and directs the operator to recovery/audit.

It must never read task rows from `ai-status.json`, archived projections, or a
product API to populate the new journal.

### 6.4 Tests

- provider paths resolve under two different home directories;
- custom and default deployment roots;
- dry-run has no writes;
- second bootstrap run is idempotent;
- sandbox preflight fails before runtime sealing;
- health compares exact command-root SHA/config/argv;
- empty journal accepts one genesis event;
- non-empty, partial, and invalid journals refuse genesis;
- no projection-to-journal recovery path exists.

## 7. PSD-06 — integrated acceptance and evidence

### 7.1 Local/CI matrix

| Plane | Required evidence |
|---|---|
| BFF | architecture, route inventory, auth, smoke, domain regression |
| ingestion | connector policy plus complete source-ingestion suite |
| queue | corruption fixture, restart, sustained processing |
| bootstrap | no-token, valid-token, rejected-token paths |
| development tooling | bootstrap idempotence, command-root health, journal guards |
| delivery | exact PR head, checks, merge commit, `dev` ancestry |

### 7.2 Hosted sequence

1. Record accepted Pantheon `dev` SHA.
2. Deploy that exact SHA to the replacement dev environment.
3. Read back the hosted deployment manifest; do not infer identity from the
   remote branch.
4. Verify BFF readiness contract and route smoke.
5. Verify source ingestion starts without disabled-source egress.
6. Verify no public quick tunnel appears without opt-in.
7. Execute telemetry bootstrap no-token behavior and an authorized replay in a
   controlled diagnostic window.
8. Exercise queue corruption recovery using a non-production fixture.
9. Record rollback target and prove rollback does not select an unaccepted
   candidate.

### 7.3 Evidence schema

The closeout evidence must include:

```json
{
  "baseline_dev_sha": "40-hex",
  "accepted_dev_sha": "40-hex",
  "release_tag": "string",
  "promotion_merge_sha": "40-hex",
  "hosted_backend_sha": "40-hex",
  "hosted_frontend_sha": "40-hex-or-null",
  "task_prs": [{"task_id": "string", "pr": 0, "head_sha": "40-hex", "merge_sha": "40-hex"}],
  "validation": [{"plane": "string", "command_or_probe": "string", "result": "pass|fail|skipped", "artifact": "path-or-url"}],
  "rollback_target": "40-hex",
  "observed_at": "UTC RFC3339"
}
```

No credential, token, raw environment dump, or mutable `latest` identifier is
allowed in evidence.

## 8. Rollout and rollback

- Land PSD-03, PSD-04, and PSD-02 as small independent changes.
- Rebase PSD-01 last because it touches the BFF composition hotspot.
- Keep PSD-05 on the development-tooling delivery path; its merge proves
  reconstructability, not product readiness.
- Deploy only a SHA containing all accepted packets.
- Before switching the hosted target, verify readiness and retain the prior
  accepted release as rollback target.
- On failure, switch back to the prior exact release; do not mutate the failed
  candidate in place and continue claiming it as the same artifact.

## 9. Definition of done

The design is complete when all packet PRs are merged or explicitly
superseded, CI and hosted validations pass, the hosted manifest names the exact
accepted identities, rollback is available, and the GAP report can mark
PSD-GAP-01 through PSD-GAP-05 closed with direct evidence.
