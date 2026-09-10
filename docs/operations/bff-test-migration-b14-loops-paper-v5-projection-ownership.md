# B14 loops / paper V5 projection ownership decision

Task: `BFF-LOOPS-PAPER-V5-PROJECTION-OWNERSHIP-DECISION-001`
Owner: Codex · independent reviewer: Antigravity
Audited source: `fb142e4f4e9289a984e42e563de16ce58c079b71` (`origin/dev`, 2026-09-10)
Status: decision proposed for exact-head review; source extraction is not delivered.

## Decision and boundary

**Retain `services/control-plane/bff/personas/service.py` as the single canonical
owner of persona source-health and unavailable trading-performance projections.**
Move the remaining execution health builder into that owner. The composition
root and runtime routes consume the same explicit `PersonaService` instance.
This reuses the existing domain owner and its context-bound read surface.
There will be one projection implementation and one cache per service instance,
shared by the application's persona and runtime consumers.

This task changes only this document and its [evidence manifest](../deployment/evidence/BFF-LOOPS-PAPER-V5-PROJECTION-OWNERSHIP-DECISION-001/evidence.json).
It does not grant production edits or migrate B14 tests. The repartition plan is
already done; B14's source seam is a separate prerequisite. Canonical task state
and PR review evidence remain authoritative for approval and completion.

Rejected alternatives:

| Alternative | Reason |
| --- | --- |
| Copy the helpers into `runtime/service.py` | Leaves the two current implementations and creates a third mechanism; runtime is a consumer of persona projections. |
| Move only the main copy into runtime | Persona DTOs keep another implementation/cache, and runtime still depends on a main-owned builder. |
| New forwarding projection module or compatibility wrapper | Adds another ownership surface without retiring the existing copies. Use direct imports or bound method references at composition. |
| Import `main` from persona/runtime code, including dynamic imports | Reverses the dependency direction and retains the test-coupling defect. |
| Replace the health builder with a fake callback in B14 tests | Removes the paper honesty regression instead of providing its real injectable implementation. |

## Current source trace

All line numbers below refer to the audited source commit. The manifest records
SHA-256 file hashes, per-function body hashes, all 83 symbol references found in
619 BFF Python files, and the builder's complete free-name inventory.

| Symbol | `main.py` lines | `personas/service.py` lines | AST body comparison |
| --- | --- | --- | --- |
| `_trading_performance_delta` | 19957–19960 | 13713–13716 | Exact match; always `None`. |
| `_source_ingest_truth_by_connector` | 19983–20025 | 13744–13786 | Only read-port expression differs. |
| `_connector_candidates_for_provider` | 20026–20034 | 13797–13805 | Exact match. |
| `_source_failure_reason` | 20035–20048 | 13808–13821 | Exact match. |
| `_provider_status_from_truth` | 20049–20057 | 13824–13832 | Exact match. |
| `_source_truth_projection` | 20058–20122 | 13835–13899 | Exact match. |
| `_select_source_truth` | 20123–20131 | 13902–13910 | Exact match. |
| `_source_health_bindings_from_requirements` | 20132–20167 | 13913–13948 | Exact match. |
| `_data_source_ok_tone` | 20168–20170 | 13951–13953 | Exact match. |
| `_upgrade_all_green_data_source_state` | 20171–20185 | 13956–13970 | Exact match. |
| `_overlay_source_health_truth` | 20186–20267 | 13973–14054 | Exact match. |

Normalization first compares `ast.dump(Module(function.body),
include_attributes=False)`: it removes source positions, comments and formatting,
but preserves docstrings, constants, statement order and expressions. Ten of eleven
bodies match without any semantic replacement. A separately reported comparison
replaces only zero-argument `_get_active_read_store()` with `read_store`; the
eleventh body then matches too. This does **not** prove that their runtime stores
or caches are currently the same object.

### Helper and cache closure

Both source-health closures depend only on their own helpers, `json`, `time`,
typing names, builtins, the read port, and three equal-valued globals:
`_SOURCE_HEALTH_OVERLAY_CACHE = {"at": 0.0, "by_connector": None}`,
`_SOURCE_HEALTH_OVERLAY_TTL = 60.0`, and the provider-to-connector candidate map
recorded in the manifest. The two dicts are separate allocations.

The loader uses `time.monotonic()`, checks `truth_by_connector` (not the initial
`by_connector` key), and reads `get_source_connector_registry()` and
`get_source_health_usage_snapshot()`. Each read has independent exception
containment; a failing enrichment must not fail the persona surface. It deep
copies JSON data, joins on connector/source IDs, then writes `at`,
`truth_by_connector`, and the health-only `by_connector` view. Empty truth is
also cached. The service additionally defines `_live_source_health_by_connector`
at 13789: it derives the health-only view from that loader. There are no direct
production callers of that helper in the audited BFF tree; an old test still
patches that name on main even though main no longer defines it.

Main resolves `read_store` directly. Persona service resolves explicit store,
then `_current_persona_service.get().get_read_store()`, then legacy module
`read_store` (initially `None`), at 231–237. Its current module-global cache is
not isolated by service instance. The extraction must make cache ownership
explicit; copying the global into another module would preserve that defect.

### Callers and runtime injection

| Producer / consumer | Current path |
| --- | --- |
| Main persona DTO | `_project_persona_dto` → overlay, line 9595. |
| Main execution health list | `_build_persona_health_items` → overlay at 20637 and delta at 20769/20770. |
| Persona DTO | service `_project_persona_dto` → overlay at 2096. |
| Persona fleet list | service `_project_persona_fleet_list_row` → overlay at 7773 and delta at 7861/7862. |
| Main generic list | `_sem_final_generic_list_for_path` → health builder at 21169. |
| Runtime V5 persona-health route | main 22059–22105 creates router with `read_surface=app_deps.read_surface` and dependency pair `("_build_persona_health_items", _build_persona_health_items)` at 22069. |

`runtime/router.py:44` resolves that callback through
`RuntimeRouterService.dependency`; `/bff/v5/execution/persona-health` calls it at
1229 with `include_market_persona_defaults=True`. `runtime/service.py` only
stores/resolves injected dependencies; missing callbacks raise when used. It
does not implement either projection. Route discovery or a stubbed callback
does not establish production wiring.

The scanned direct test consumers of the two projections are
`test_p0_tw_paper_activate_honesty.py`, `test_loop_auto_bff004_cross_loop_drill.py`,
`test_srclive_overlay_contract.py`, and
`test_pathreon_market_persona_fleet_contract.py`. The last three patch main's
truth loader. `test_bff_promotion_review_governance.py` also patches that loader
to assert no detail-only subread. Composition tests mention the delta/builder
as allowed exports. Full reference locations, including literal injection and
monkeypatch names, are in the manifest; these tests must not be silently ignored
when the production owner moves.

### The builder must move with the seam

`main.py:20468–20862` has the only `_build_persona_health_items` implementation.
Moving just the two duplicate functions would leave B14's paper honesty test
coupled to main. Of its 21 direct local helper calls, 19 already have service
counterparts with equal bodies after the narrowly defined read-port
normalization. `_first_binding_for_persona` (19884–19904) and `_runtime_for_pool`
(19905–19917) have no counterpart and must move into the service as read-only
helpers. Preserve their active/ready/bound preference and default-persona flag.

The other helpers cover numeric parsing and aggregation, persona identity and
health, OODA/autonomy labels, telemetry rollup, context defaults/overlay,
incident filtering, capital-mode/pool/ledger presentation, mutation presentation,
and routed-strategy counts. Reuse the service counterparts; do not copy their
main definitions again. The differing context-default and strategy-count bodies
also differ only in the read-port expression. Their recursive dependencies
already resolve inside the existing service module. Keep unrelated main callers
working; do not expand this seam into general fleet, capital or journal cleanup.

## Exact future source-extraction packet

This is a proposed task specification, not a canonical task creation or grant.
The supervisor/development bridge must materialize it after the gates below.

- Task ID: `BFF-LOOPS-PAPER-V5-PROJECTION-SEAM-CORRECTIVE-001`.
- Target: `ajoe734/pantheon`, task branch from then-current `dev`, merge to `dev`.
- Owner: Codex; independent reviewer: Antigravity. Revalidate their canonical
  assignment/authentication at dispatch; these names do not imply shared identity.
- Prerequisites: this ownership decision and the accepted B03 CORS/JWKS/MFA
  source-seam task. Preserve all existing B14 prerequisites when adding this
  seam upstream of `BFF-TEST-MIGRATION-B14-LOOPS-PAPER-V5-001`.
- The B03 source-seam ID must be resolved from its governed admission; no task
  ID or completed status is fabricated here. No dispatch with a placeholder edge.

Exact proposed writable artifacts (no wildcard grants):

1. `services/control-plane/bff/main.py`
2. `services/control-plane/bff/personas/service.py`
3. `services/control-plane/bff/personas/test_health_projection.py` (new focused service tests)
4. `services/control-plane/bff/personas/test_health_projection_wiring.py` (new static composition and real-router injection tests)
5. `docs/deployment/evidence/BFF-LOOPS-PAPER-V5-PROJECTION-SEAM-CORRECTIVE-001/evidence.json`
6. `services/control-plane/bff/test_p0_tw_paper_activate_honesty.py`
7. `services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py`
8. `services/control-plane/bff/test_srclive_overlay_contract.py`
9. `services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py`
10. `services/control-plane/bff/test_bff_promotion_review_governance.py`

`runtime/router.py` and `runtime/service.py` are read-only consumers for this
packet: the existing callback key/signature suffices. Items 6–10 are explicit
**proposed temporary file transfers**, required before this packet may execute;
the current docs task has no grant to edit them. The transfer protocol below
supersedes the previous instruction to leave all consumer fixes downstream.
B14's evidence, parent catalog/gates, and every other test remain outside this
packet. If preflight finds another required edit, revise the governed contract
before execution; no compatibility wrapper may hide a missing grant.

### Atomic consumer migration and governed file transfers (P1 correction)

**Delete main's projection definitions/loader/cache only in the same reviewed
source-seam PR that updates every affected test call/import/patch below.** A
bound overlay alias does not redirect a monkeypatch of main's old loader into
the service instance. A direct delta import alone does not solve that problem.
There is no supported intermediate merge with inert patches, absent symbols,
or a source seam waiting on its downstream B14 consumer.

Paths in this table are relative to `services/control-plane/bff/`. Historical
partition IDs and full paths remain recorded in the evidence
`consumer_migration` section. They are provenance, not current write authority.
Canonical readback confirms `BFF-TEST-MIGRATION-B15-DEPLOYMENT-HOSTED-001` is
`done` with `terminal_outcome=superseded` and an empty artifact contract.
The live transfer/restore/dependency target for `test_srclive_overlay_contract.py`
is `BFF-TEST-MIGRATION-B15-DEPLOYMENT-HOSTED-REMAINDER-001`.
Preserve its prerequisite `BFF-TEST-MIGRATION-B15-WHOLE-APP-ALLOWLIST-CONTRACT-CORRECTIVE-001`
as well as repartition; the parent already depends on both B15 successors.
No operation in this proposal targets the superseded row. In the rest of this
document, B15 means the live remainder unless explicitly labelled historical.
The future whole-app correction separately reconciles 184 historical files to
182 non-allowlist importers; this decision preserves the current 184-file
partition and eleven-file B14 scope without claiming that correction delivered.

| Exact test file | Current partition owner | Source-seam edit and required assertion coverage |
| --- | --- | --- |
| `test_p0_tw_paper_activate_honesty.py` | B14 | Import the canonical service delta; call the real instance builder with an explicit empty read fixture. Retain unavailable return, TW default persona, seed and telemetry assertions. |
| `test_loop_auto_bff004_cross_loop_drill.py` | B14 | Replace the main loader patch and overlay call in the source-to-health drill with the real instance overlay and explicit registry/health snapshot fixture. Preserve provider, binding, health-source and live-ingestion assertions; leave other drills for B14. |
| `test_srclive_overlay_contract.py` | B15 live remainder (historical partition: B15) | Replace all five main-loader patches/overlay calls with explicit service/read-port fixtures. Retain all-green summary, TW/US/crypto connector mapping, unavailable credential and missing-truth assertions. |
| `test_pathreon_market_persona_fleet_contract.py` | B07 | Migrate all source-truth patches/overlay calls and the two obsolete FinMind overlay tests to the canonical instance and read-port fixture. Preserve credential/degraded behavior, source mapping, row count, live status and missing-truth intent as detailed below; unrelated fleet routes remain B07 work. |
| `test_bff_promotion_review_governance.py` | B05 | Replace the readiness test's forbidden main-loader patch with observable counters at both injected read-port methods `get_source_connector_registry` and `get_source_health_usage_snapshot`. Assert zero calls as well as the existing two batched reads and forbidden fleet subreads. Its unrelated journal/router migration stays downstream. |

The two FinMind tests already name absent `_live_source_health_by_connector`
and `_overlay_live_finmind_health` exports in the audited source. Do not restore
them or treat this pre-existing failure as a pass. Record baseline outcomes and
an explicit assertion correspondence in the seam evidence: live FinMind still
becomes `read_ok`; the current canonical all-green state is
`live_readback_ok`; row count 8 is asserted on the FinMind source's
`row_count_last_run` (and camel alias), replacing the obsolete top-level
`finmind_live_row_count_last_run`; no truth preserves the original unavailable
provider/state. These mappings follow the existing service projection and
`_upgrade_all_green_data_source_state`, not a production DTO change. Require
independent review of this correspondence; no skipped/xfail tests, deleted
coverage, or restored legacy wrapper is acceptable.

Use fixtures that exercise the real loader through its registry and snapshot
ports, not a patched loader/method result. For the readiness negative-read
test, record calls independently of thrown exceptions because enrichment
contains exceptions; include a fixture control proving those counters increment
when the real overlay is invoked on a fresh service. Preserve the original
endpoint request/output checks while replacing its obsolete negative sentinel.
The static composition suite's delta/builder strings are an export allowlist,
not imports or patches; it stays read-only and must pass unchanged. At source
admission, rescan all references, including `_overlay_live_finmind_health`,
and reject any newly discovered unassigned consumer.

The supervisor/development bridge must arrange for authorized Human/Ops contract
admission through governed artifact/dependency-contract operations with fresh
complete-row CAS digests; this document is a proposal and performs none of them:

1. Read the current canonical rows, dependencies, exact artifacts, open PRs and
   leases for the seam, B05/B07/B14/B15 and their admitted corrective writers.
   In particular reconcile B14 PR #5751 and the proposed B05 journal seam's
   overlapping governance test grant. Let an existing writer finish and release
   its lease or obtain an explicit governed handback before transfer.
2. Artifact-contract edits are legal only for `todo`/`blocked` rows, with no
   terminal fact or immutable conflict guard. An active writer must first finish
   or return through a governed handback to an eligible state; never mutate an
   active/review/terminal contract or reopen a terminal task merely to transfer
   files. Terminal history does not hold a live lease. Temporarily remove items
   6–10 from eligible overlapping writer grants before adding
   those exact paths to this source-seam task. Retain each batch's other files,
   evidence and prerequisites. Only the seam holds these five file leases during
   extraction, and acceptance limits its edits to the projection consumers above.
   Record removals/additions and original destination tasks in the seam admission
   evidence. Partial contract application is not dispatch-ready.
3. Add the seam as a prerequisite of B05, B07, B14 and B15, preserving their
   existing prerequisites. Serialize the B05 journal corrective behind the seam
   as well before restoring its overlapping grant. The seam depends only on
   this decision, the accepted B03 source seam and their upstream prerequisites;
   it must not depend on any of those consumer batches, their corrective writers,
   or the parent migration. Validate the **entire current canonical graph** for
   cycles before dispatch, not just this proposed subgraph. A pre-existing path
   from B03 to a consumer/parent needs a governed graph correction first.
4. Deliver production extraction and the five bounded consumer edits atomically
   at one exact reviewed head. Validate all five full files plus the focused
   service/router/composition suites; preserve collection and assertion coverage.
   A partial patch or uncollected/failing batch does not authorize symbol removal
   to merge. Record any pre-existing failures separately; do not claim them green.
5. After exact reviewed merge, canonical `done` readback and released seam
   leases, **leave the seam's terminal artifact contract unchanged** as immutable
   delivery history. Do not issue post-done artifact removal. Restore the five
   paths only to their live destination tasks, which must still be `todo` or
   `blocked`, using fresh complete-row CAS and conflict/lease checks. In
   particular restore the source-live test to the B15 remainder, never old B15.
   A terminal destination requires a separately admitted successor, not reopening
   its history. Restore the B05 corrective overlap only under its serialized
   lease. Read back all destination contracts and terminal seam truth before
   redispatch; any partial restoration leaves affected consumers undispatched. Rebase
   consumer PRs on the merged seam; they retain full batch acceptance and may
   not restore main projection patches. This transfer does not mark a batch done
   or alter the 184-file final acceptance universe or the eleven-file B14 run.

This order is cycle-free by construction for the added edges: decision + B03
source seam → atomic B14 source seam → B05/B07/B14/B15 → parent. Other main
writers still follow the serial order below. Neither a downstream migration nor
parent cleanup unlocks the source seam. No worker may infer these transfers
from this document without canonical admission readback.

### API and dependency direction

Retain the module function `_trading_performance_delta() -> Optional[float]`
in `personas/service.py` as the sole implementation. Main uses an explicit
module import (`from personas import service as persona_projection`) and calls
`persona_projection._trading_performance_delta()` at residual delta call sites;
it must not export the old name, even as an imported alias. It returns `None`
until a separate canonical return-schema decision changes that contract.

Expose these read-only service methods, placing the actual implementation in
this owner (not another forwarding module):

```python
PersonaService.overlay_source_health_truth(
    self, data_source_status: Any, data_sources: Any, *,
    required_data_sources: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]

PersonaService.build_persona_health_items(
    self, snapshot_at: str, *, include_market_persona_defaults: bool = False,
) -> List[Dict[str, Any]]
```

Move the loader and cache into the service instance. Constructor keyword
`source_health_clock: Callable[[], float] = time.monotonic` permits deterministic
TTL tests without patching globals. Keep the 60-second TTL and initial cache
shape; clear neither another service's cache nor a shared module dict. Service
methods use `self.get_read_store()` and set/reset the existing persona context
with `try/finally` where legacy internal helpers need it, including nested calls
and exceptions. Context resolution selects the instance; it must not become a
second cache. Existing persona DTO/list helper call sites resolve that service
and invoke its method. No module-global fallback cache is retained.

In main, construct the existing `PersonaService` once, earlier than runtime
router assembly, using the same `app_deps` read/command/write-owner ports now
passed at 22514. Replace the later construction with reuse of that instance.
Update generic-list and DTO call sites to call
`persona_service.build_persona_health_items(...)` and
`persona_service.overlay_source_health_truth(...)` directly. Delete main's
duplicate overlay/helper/cache block and delta body; **no old-name aliases,
imports, wrappers or fallback globals remain**. Retire the old service module
overlay body once moved into its method. Inject the existing runtime key as
`("_build_persona_health_items", persona_service.build_persona_health_items)`
directly; that protocol string is the only main legacy-name literal exception.
Leave the route, envelopes and read surface unchanged.

Dependency flow: main constructs service → injects its bound method into runtime;
persona routes use the same service → service reads supplied ports. The service
never imports main/runtime to resolve a helper. The runtime never imports main
or creates a second projection service. No new endpoint, write command, hosted
configuration, source provider, credential handling or capital behavior is added.

### Behavioral acceptance and focused verification

The new service tests must use explicit in-memory read/command/write-owner
fixtures and the real service implementation, without importing main or patching
its globals. The new wiring test must statically prove the actual main
constructor/direct-call/dependency assignments and execute `create_runtime_router`
with a real service method and explicit unrelated route ports. A fake projection
callback alone is insufficient. Verify:

1. Exactly one delta definition and one overlay implementation; no duplicate
   cache globals, reverse imports, third wrapper, or runtime projection body.
   Copy the executable no-legacy-main gate below into the new scoped
   `personas/test_health_projection_wiring.py` and require zero violations at
   the extraction head. Retired definitions, aliases and all five consumers'
   old projection imports/calls/patches must fail that test.
2. `None` for delta and both `perf_delta`/`perfDelta`; do not substitute PnL,
   training improvement, or zero for an unavailable trading-return schema.
3. Existing paper seed/default-persona behavior, telemetry flags, filtering,
   ordering and capital presentation from the real health builder are preserved.
4. JSON-copy/nonmutation behavior, tuple return, all snake/camel DTO aliases,
   `bff_source_health_truth.v1`, connector choice/order, failure/timestamp
   precedence, usage/recommendation fields, and required-source bindings match.
5. Registry-only sources preserve original unavailable status/reason/secret-ref;
   `credential_unavailable` upgrades only on live health `ok`, not degraded.
   No-truth rows stay static; seed-only requirements stay
   `seed_only_not_live_binding`; all-green provider states retain current upgrade
   behavior. Preserve the existing binding-derived `has_live_truth` rule even
   for registry-only requirements; any semantic correction is a separate task.
6. Independent registry/snapshot failures are contained; missing/malformed rows,
   health-only data, registry-only data and empty data keep existing results.
   Test first fetch, hit before TTL, refresh at 60 seconds, cached empty result,
   shared instance across main/persona/runtime, and isolation between two services.
7. Real `/bff/v5/execution/persona-health` output and metadata use the extracted
   builder with `include_market_persona_defaults=True`; an unbound runtime
   callback continues to fail on use. No weakening of architectural import gates.

Future bounded commands, **not executed or claimed passed by this docs task**:

```bash
python3 scripts/dev/provision_python_distribution.py
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
timeout 180 "$PANTHEON_PY" -m pytest -q \
  services/control-plane/bff/personas/test_health_projection.py \
  services/control-plane/bff/personas/test_health_projection_wiring.py \
  services/control-plane/bff/personas/test_router_isolation.py
timeout 180 "$PANTHEON_PY" -m pytest -q \
  services/control-plane/bff/test_bff_runtimes_contract.py \
  services/control-plane/bff/test_pkt010_runtime_state_board_contract.py \
  services/control-plane/bff/tests/test_bff_main_composition.py
timeout 180 "$PANTHEON_PY" -m pytest -q \
  services/control-plane/bff/test_p0_tw_paper_activate_honesty.py \
  services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py \
  services/control-plane/bff/test_srclive_overlay_contract.py
timeout 180 "$PANTHEON_PY" -m pytest -q \
  services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py \
  services/control-plane/bff/test_bff_promotion_review_governance.py
```

After that seam merges, B14 migrates its eleven exact partition files to real
service/router seams, including the honesty test and cross-loop source-health
drill. Its focused run must collect all eleven files and preserve each assertion.
The five projection consumer edits already land atomically with the seam;
restored batch owners handle only their remaining migrations and full acceptance.
Do not count tests as passing because their patched globals became unused. Source-seam
and B14 manifests record separate outcomes; source-seam completion is not B14
migration completion.

## Main writer order and delivery gates

The dispatch SA/SD explicitly defers production extraction until B03's open-PR
gate clears. GitHub readback on 2026-09-10 found #5494 OPEN, #5517 CLOSED
(unmerged), #5577 OPEN, and #5721 OPEN. Thus the B03 gate is not cleared.
Recheck all four at admission; a closed PR satisfies that particular open-PR
condition but is not evidence that its code was delivered.

The required serial order is:

1. Those four PRs merge/close; perform fresh exact-artifact and active-lease
   preflight on current dev, including any newly admitted main writers.
2. B03 CORS/JWKS/MFA source seam receives the sole main writer lease, is
   independently reviewed, merges, and releases its lease. The separate B03
   management/session test harness does not satisfy this source-seam gate.
3. After this decision is also approved/merged, the B14 source-seam packet above
   receives that lease and delivers the single service owner.
4. B05's `BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001`, B06's eventual seam, and every
   other main writer run one at a time afterwards, subject to their own gates.
   B05's merged decision PR #5758 grants no concurrent production lease.

If a writer already owns main at preflight, let that admitted work finish before
starting this sequence; do not steal a lease or implement two layers of main
concurrently. Serialize any overlapping `personas/service.py` writer too. Use
governed task dependencies/resource admission, retain existing prerequisite
edges, and check for cycles before materialization. Keep the B14 seam upstream
of B14 and the parent migration; never depend on a downstream parent-dependent
cleanup task to unlock B14. Current B14 PR #5751 and B03 PR #5749 are blocked
test/evidence work, not source grants; reconcile them through their owners.

This decision follows the normal PR → independent exact-head review → required
checks → supervisor integration → canonical owner closeout flow. The manifest
is committed before handoff. Review/merge/readback identities live in the frozen
canonical binding and GitHub; do not add a post-approval bookkeeping commit or
invent a merge SHA in this pre-review manifest.

## Reproducing the current-source audit

Run the Python block below from the audited checkout. It only reads source and
prints JSON. Compare it to `source_audit` in the manifest. An updated dev requires
a fresh audit; source line numbers are not assumed stable.

<!-- B14_AUDIT_BEGIN -->
```python
import ast
import builtins
import copy
import hashlib
import json
from pathlib import Path

BFF = Path('services/control-plane/bff')
paths = [BFF / 'main.py', BFF / 'personas/service.py']
trees = [ast.parse(p.read_text()) for p in paths]
funcs = [{n.name: n for n in t.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))} for t in trees]
names = ['_trading_performance_delta', '_source_ingest_truth_by_connector',
         '_connector_candidates_for_provider', '_source_failure_reason',
         '_provider_status_from_truth', '_source_truth_projection', '_select_source_truth',
         '_source_health_bindings_from_requirements', '_data_source_ok_tone',
         '_upgrade_all_green_data_source_state', '_overlay_source_health_truth']

class ReadPort(ast.NodeTransformer):
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == '_get_active_read_store' and not node.args and not node.keywords:
            return ast.Name(id='read_store', ctx=ast.Load())
        return self.generic_visit(node)

def body(node, normalize_port=False):
    result = ast.Module(body=copy.deepcopy(node.body), type_ignores=[])
    if normalize_port:
        result = ReadPort().visit(result)
    return ast.dump(result, include_attributes=False)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

pairs = []
for name in names:
    a, b = [f[name] for f in funcs]
    raw = [body(a), body(b)]
    norm = [body(a, True), body(b, True)]
    assert norm[0] == norm[1], name
    assert (raw[0] == raw[1]) == (name != '_source_ingest_truth_by_connector'), name
    pairs.append(dict(symbol=name, main_lines=[a.lineno, a.end_lineno],
                      persona_lines=[b.lineno, b.end_lineno],
                      body_sha256=list(map(digest, raw)), exact_body_equal=raw[0] == raw[1],
                      port_normalized_body_sha256=digest(norm[0])))

constants = []
for name in ['_SOURCE_HEALTH_OVERLAY_CACHE', '_SOURCE_HEALTH_OVERLAY_TTL', '_SOURCE_PROVIDER_CONNECTOR_CANDIDATES']:
    values = []
    for tree in trees:
        node = next(n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and
                    any(isinstance(t, ast.Name) and t.id == name for t in (n.targets if isinstance(n, ast.Assign) else [n.target])))
        values.append(ast.literal_eval(node.value))
    assert values[0] == values[1]
    constants.append(dict(symbol=name, equal=True, value=values[0]))

targets = set(names) | {x['symbol'] for x in constants} | {'_live_source_health_by_connector', '_build_persona_health_items'}
references = []
class References(ast.NodeVisitor):
    def __init__(self, path):
        self.path, self.scope = str(path), []
    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
    visit_AsyncFunctionDef = visit_FunctionDef
    def visit_Name(self, node):
        if node.id in targets and isinstance(node.ctx, ast.Load):
            self.record(node, node.id, 'name_load')
    def visit_Attribute(self, node):
        if node.attr in targets:
            self.record(node, node.attr, 'attribute')
        self.generic_visit(node)
    def visit_Constant(self, node):
        if isinstance(node.value, str) and node.value in targets:
            self.record(node, node.value, 'literal_dependency_or_patch')
    def record(self, node, symbol, kind):
        references.append(dict(path=self.path, line=node.lineno, scope='.'.join(self.scope) or '<module>', symbol=symbol, kind=kind))

scanned = 0
for path in sorted(BFF.rglob('*.py')):
    References(path).visit(ast.parse(path.read_text()))
    scanned += 1

builder = funcs[0]['_build_persona_health_items']
loads = {n.id for n in ast.walk(builder) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
locals_ = {n.id for n in ast.walk(builder) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
locals_ |= {n.arg for n in ast.walk(builder) if isinstance(n, ast.arg)}
closure = []
for name in sorted(loads - locals_ - set(dir(builtins))):
    a, b = funcs[0].get(name), funcs[1].get(name)
    closure.append(dict(symbol=name, main_line=getattr(a, 'lineno', None), persona_line=getattr(b, 'lineno', None),
                        port_normalized_equal=body(a, True) == body(b, True) if a and b else None))

print(json.dumps(dict(source_files=[dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in
                                  paths + [BFF / 'runtime/router.py', BFF / 'runtime/service.py']],
                      normalization='ast.dump(Module(function.body), include_attributes=False); separate explicit normalization replaces only zero-argument _get_active_read_store() with read_store',
                      duplicate_functions=pairs, duplicate_constants=constants,
                      builder_free_names=closure, scanned_python_files=scanned, references=references), indent=2, sort_keys=True))
```
<!-- B14_AUDIT_END -->

### Reproduce the P1 consumer contract checks

Run this read-only block from the same checkout. It validates the proposed
transfers against the accepted partition, the complete inventoried consumer
set, and the full captured canonical dependency graph with a proposed overlay.
The separate live-read block below refreshes that snapshot without mutations.
Neither check is source admission or a future product test run.

<!-- B14_CONSUMER_CHECK_BEGIN -->
```python
import hashlib
import json
from graphlib import TopologicalSorter
from pathlib import Path

manifest = Path('docs/deployment/evidence/BFF-LOOPS-PAPER-V5-PROJECTION-OWNERSHIP-DECISION-001/evidence.json')
d = json.loads(manifest.read_text())
m = d['consumer_migration']
p = Path(m['partition_path'])
assert hashlib.sha256(p.read_bytes()).hexdigest() == m['partition_sha256']
partition = json.loads(p.read_text())['candidate_children']
rows = m['transfers']
paths = {r['path'] for r in rows}
assert len(rows) == len(paths) == 5
references = {r['path'] for r in d['source_audit']['references'] if '/test_' in r['path']}
references.remove('services/control-plane/bff/tests/test_bff_main_composition.py')
assert paths == references
seam = d['future_packet']['id']
graph = m['proposed_dependency_graph']
order = list(TopologicalSorter(graph).static_order())
for r in rows:
    batch, = [b for b in partition if r['path'] in b['source_artifacts']]
    assert batch['task_id'] == r['partition_task']
    expected = m['b15_successor']['live_task'] if r['partition_task'] == m['b15_successor']['historical_partition_task'] else r['partition_task']
    assert r['transfer_from_task'] == r['restore_to_task'] == expected
    assert r['temporary_owner_task'] == seam
    assert r['path'] in d['future_packet']['artifacts']
    assert seam in graph[r['restore_to_task']]
    assert order.index(seam) < order.index(r['restore_to_task'])
readback = m['canonical_graph_readback']
list(TopologicalSorter(readback['current_graph']).static_order())
for task, deps in readback['current_graph'].items():
    assert set(deps) <= set(graph[task]), task
b15 = m['b15_successor']
assert b15['required_prerequisite'] in graph[b15['live_task']]
assert {b15['live_task'], b15['required_prerequisite']} <= set(graph[b15['parent']])
assert b15['historical_partition_task'] not in graph[b15['parent']]
assert m['terminal_contract_policy']['seam_after_done'] == 'immutable; no artifact removals'
assert m['terminal_contract_policy']['mutable_states'] == ['todo', 'blocked']
def ancestors(node):
    return {p for p in graph.get(node, [])} | {
        a for p in graph.get(node, []) for a in ancestors(p)}
assert not ancestors(seam).intersection(m['no_reverse_dependency'])
assert sum(len(b['source_artifacts']) for b in partition) == 184
assert len(next(b for b in partition if b['group'] == 'B14_loops_paper_v5')['source_artifacts']) == 11
assert hashlib.sha256(Path(d['future_packet']['specification']).read_bytes()).hexdigest() == d['decision_document_sha256']
print('PASS: five live consumer transfers, historical partition provenance, all canonical prerequisites, full captured DAG and conditional overlay, terminal immutability, 184/11 counts, document hash')
```
<!-- B14_CONSUMER_CHECK_END -->


### Executable no-legacy-main acceptance gate

The following gate is part of the **future extraction packet**, to be copied
into its declared `personas/test_health_projection_wiring.py`. Run
`test_gate_controls` and `test_no_legacy_main_projection_surface` under pytest
there; zero violations is mandatory before symbol removal can merge. Main may
call the canonical delta through its module, but may not bind/export any retired
name. Its one runtime dependency-key string is allowed only when paired directly
with the actual service method. Consumer imports, aliases, attributes and patch
strings naming retired projections fail, including obsolete FinMind helpers.
Only a delta import from the canonical service is permitted in those tests.
Other main route fixtures in the five files remain their batches' separate
migration scope; they do not authorize any legacy projection patch/import.
Existing AST/dynamic-import/subprocess inventory gates remain required.

For this docs task the script entry point runs positive/negative checker controls
and reports current violations. It deliberately does **not** call the future
zero-violation test: current production has not been extracted and all six
scanned files must still report baseline violations. This proves gate sensitivity,
not product acceptance. The existing composition allowlist stays read-only.

<!-- B14_NO_LEGACY_BEGIN -->
```python
import ast
import json
import re
from pathlib import Path

RETIRED = {
    '_trading_performance_delta', '_source_ingest_truth_by_connector',
    '_connector_candidates_for_provider', '_source_failure_reason',
    '_provider_status_from_truth', '_source_truth_projection', '_select_source_truth',
    '_source_health_bindings_from_requirements', '_data_source_ok_tone',
    '_upgrade_all_green_data_source_state', '_overlay_source_health_truth',
    '_SOURCE_HEALTH_OVERLAY_CACHE', '_SOURCE_HEALTH_OVERLAY_TTL',
    '_SOURCE_PROVIDER_CONNECTOR_CANDIDATES', '_build_persona_health_items',
    '_live_source_health_by_connector', '_overlay_live_finmind_health',
}
DELTA = '_trading_performance_delta'
BUILDER = '_build_persona_health_items'

def legacy_violations(source, *, composition=False):
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    module_aliases = {a.asname or a.name for n in ast.walk(tree)
                      if isinstance(n, ast.ImportFrom) and n.module == 'personas'
                      for a in n.names if a.name == 'service'}
    delta_aliases = {a.asname or a.name for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module == 'personas.service'
                     for a in n.names if a.name == DELTA} if not composition else set()
    failures = set()
    def reject(node, name):
        failures.add((node.lineno, name))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in RETIRED:
            reject(n, n.name)
        elif isinstance(n, ast.Name) and n.id in RETIRED:
            if not (isinstance(n.ctx, ast.Load) and n.id in delta_aliases):
                reject(n, n.id)
        elif isinstance(n, ast.alias):
            parent = parents[n]
            canonical_delta = (not composition and isinstance(parent, ast.ImportFrom)
                               and parent.module == 'personas.service' and n.name == DELTA)
            if not canonical_delta:
                for name in (n.name, n.asname):
                    if name in RETIRED:
                        reject(n, name)
        elif isinstance(n, ast.Attribute) and n.attr in RETIRED:
            if not (n.attr == DELTA and isinstance(n.value, ast.Name)
                    and n.value.id in module_aliases):
                reject(n, n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            # Ignore docstrings only; patches, getattr, globals, exec/import strings count.
            parent = parents.get(n)
            grandparent = parents.get(parent)
            if (isinstance(parent, ast.Expr) and
                isinstance(grandparent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and grandparent.body[0] is parent):
                continue
            key = (composition and n.value == BUILDER and isinstance(parent, ast.Tuple)
                   and len(parent.elts) == 2 and parent.elts[0] is n
                   and ast.dump(parent.elts[1], include_attributes=False) ==
                   ast.dump(ast.parse('persona_service.build_persona_health_items', mode='eval').body,
                            include_attributes=False))
            for name in RETIRED:
                if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', n.value) and not key:
                    reject(n, name)
    return sorted(failures)

def test_gate_controls():
    for name in RETIRED:
        for bad in (f'def {name}(): pass', f'{name} = service.method',
                    f'from main import {name} as hidden', f'alias.{name}()',
                    f'monkeypatch.setattr(alias, "{name}", stub)',
                    f'globals()["{name}"] = stub'):
            assert legacy_violations(bad, composition=True), bad
            assert legacy_violations(bad), bad
    assert legacy_violations('from personas.service import _trading_performance_delta', composition=True)
    assert not legacy_violations('from personas.service import _trading_performance_delta\n_trading_performance_delta()')
    assert not legacy_violations('from personas import service as persona_projection\n'
                                 'persona_projection._trading_performance_delta()\n'
                                 'persona_service.overlay_source_health_truth({}, [])\n'
                                 '("_build_persona_health_items", persona_service.build_persona_health_items)',
                                 composition=True)

def scan_projection_consumers():
    e = json.loads(Path('docs/deployment/evidence/BFF-LOOPS-PAPER-V5-PROJECTION-OWNERSHIP-DECISION-001/evidence.json').read_text())
    paths = ['services/control-plane/bff/main.py'] + [r['path'] for r in e['consumer_migration']['transfers']]
    return {p: legacy_violations(Path(p).read_text(), composition=p.endswith('/main.py')) for p in paths}

def test_no_legacy_main_projection_surface():
    violations = {p: v for p, v in scan_projection_consumers().items() if v}
    assert not violations, violations

if __name__ == '__main__':
    test_gate_controls()
    print(json.dumps(scan_projection_consumers(), indent=2))
```
<!-- B14_NO_LEGACY_END -->

### Reproduce the complete canonical graph readback

Run the following observational block with `AI_NAME=Codex` and the inherited
supervisor bindings. It uses the pinned command runtime's TaskStore V2 read API,
never the worktree status mirror or the full activity history. The manifest
stores snapshot hashes, every active edge, terminal leaves and archive hashes
for terminal dependencies absent from the compact facts. Unknown/unproven
references fail closed. The checker adds proposed edges to a copy, preserves all
current prerequisites and verifies the complete graph for cycles. It does not
apply contract operations. The unresolved B03 role remains explicitly
non-executable; its real dependencies must replace that role and pass the same
full-graph check at admission. A current DAG pass cannot certify an unknown
future B03 task. Recheck eligible row states, PRs and leases separately then.

<!-- B14_FULL_GRAPH_BEGIN -->
```python
import hashlib
import json
import os
import sys
from graphlib import TopologicalSorter
from pathlib import Path

sys.path.insert(0, str(Path(os.environ['PANTHEON_COMMAND_ROOT']) / '.orchestrator'))
from rewrite.task_state_store import load_snapshot
snapshot = load_snapshot(Path(os.environ['PANTHEON_TASK_STATE_EVENT_LOG']), refresh_checkpoint=False)
state = snapshot['state']
rows = {r['id']: r for r in state['tasks']}
terminal = state['terminal_facts']
graph = {k: r.get('depends_on', []) for k, r in rows.items()}
# Terminal facts are satisfied leaves; historical dependencies do not gate dispatch.
for k in terminal:
    graph.setdefault(k, [])
archive_leaves = {}
missing = {p for deps in graph.values() for p in deps} - graph.keys()
for k in sorted(missing):
    path = Path(os.environ['PANTHEON_STATUS_ROOT']) / 'ai-task-archive/tasks' / (k + '.json')
    raw = path.read_bytes()  # Missing/unproven dependency fails closed.
    archived = json.loads(raw)
    assert archived['task_id'] == k and archived['terminal_status'] == 'done', k
    archive_leaves[k] = {'status': archived['terminal_status'], 'sha256': hashlib.sha256(raw).hexdigest()}
    graph[k] = []
list(TopologicalSorter(graph).static_order())
manifest = json.loads(Path('docs/deployment/evidence/BFF-LOOPS-PAPER-V5-PROJECTION-OWNERSHIP-DECISION-001/evidence.json').read_text())
m = manifest['consumer_migration']
seam = manifest['future_packet']['id']
proposed = {k: list(v) for k, v in graph.items()}
proposed[seam] = [manifest['task_id'], 'ROLE:accepted-B03-source-seam']
proposed['ROLE:accepted-B03-source-seam'] = []  # Unresolved, NOT dispatch authority.
for r in m['transfers']:
    target = r['restore_to_task']
    assert r['path'] in rows[target]['artifacts'], (target, r['path'])
    assert not (rows[target].get('terminal_outcome') or target in terminal), target
    proposed[target] = sorted(set(proposed[target]) | {seam})
journal = 'BFF-JOURNAL-CONTEXT-SEAM-CORRECTIVE-001'
proposed[journal] = sorted(set(proposed.get(journal, [])) | {seam})
b05 = 'BFF-TEST-MIGRATION-B05-GOVERNANCE-APPROVALS-001'
proposed[b05] = sorted(set(proposed[b05]) | {journal})
list(TopologicalSorter(proposed).static_order())
def ancestors(node):
    found, pending = set(), list(proposed.get(node, []))
    while pending:
        p = pending.pop()
        if p not in found:
            found.add(p)
            pending.extend(proposed.get(p, []))
    return found
assert not ancestors(seam).intersection(m['no_reverse_dependency'])
b15 = m['b15_successor']
assert b15['required_prerequisite'] in proposed[b15['live_task']]
assert {b15['required_prerequisite'], b15['live_task']} <= set(proposed[b15['parent']])
assert b15['historical_partition_task'] not in proposed[b15['parent']]
result = dict(event_count=snapshot['event_count'], state_sha256=snapshot['state_sha256'],
              last_event_sha256=snapshot['last_event_sha256'], active_rows=len(rows),
              terminal_facts=len(terminal), archive_terminal_leaves=archive_leaves,
              current_graph=graph, proposed_graph=proposed,
              current_graph_acyclic=True, proposed_graph_acyclic=True,
              proposed_nodes=len(proposed), unresolved_admission_role='ROLE:accepted-B03-source-seam',
              consumer_rows={k: {f: rows[k].get(f) for f in ('status','depends_on','artifacts')}
                             for k in sorted({r['restore_to_task'] for r in m['transfers']})})
print(json.dumps(result, indent=2, sort_keys=True))
```
<!-- B14_FULL_GRAPH_END -->
