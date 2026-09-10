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

`runtime/router.py` and `runtime/service.py` are read-only consumers for this
packet: the existing callback key/signature suffices. B14's eleven tests and
evidence remain B14-owned. Parent catalog/gates and the source-health/fleet tests
owned by other migration batches are not added implicitly. If preflight finds
an additional required source or test edit, revise the governed contract before
execution; no out-of-scope compatibility wrapper may hide the missing grant.

### API and dependency direction

Retain the module function `_trading_performance_delta() -> Optional[float]`
in `personas/service.py` as the sole implementation; main may directly import
the same function object during compatibility migration. It returns `None`
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
Bind `_build_persona_health_items = persona_service.build_persona_health_items`
and, if needed by residual main callers,
`_overlay_source_health_truth = persona_service.overlay_source_health_truth`.
These are object references, not new function bodies. Delete main's duplicate
overlay/helper/cache block and delta body. Retire the old service module overlay
body once moved into its method. Preserve generic-list and DTO consumers through
these direct references. Bind runtime's existing dependency key to the real
service method; leave its route, envelopes and read surface unchanged.

Dependency flow: main constructs service → injects its bound method into runtime;
persona routes use the same service → service reads supplied ports. The service
never imports main/runtime to resolve a helper. The runtime never imports main
or creates a second projection service. No new endpoint, write command, hosted
configuration, source provider, credential handling or capital behavior is added.

### Behavioral acceptance and focused verification

The new service tests must use explicit in-memory read/command/write-owner
fixtures and the real service implementation, without importing main or patching
its globals. The new wiring test must statically prove the actual main
constructor/alias/dependency assignments and execute `create_runtime_router`
with a real service method and explicit unrelated route ports. A fake projection
callback alone is insufficient. Verify:

1. Exactly one delta definition and one overlay implementation; no duplicate
   cache globals, reverse imports, third wrapper, or runtime projection body.
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
```

After that seam merges, B14 migrates its eleven exact partition files to real
service/router seams, including the honesty test and cross-loop source-health
drill. Its focused run must collect all eleven files and preserve each assertion.
Other batch owners must replace main-loader monkeypatches in the caller inventory
with explicit service fixtures under their own grants. Do not count legacy
tests as passing because their patched globals have become unused. Source-seam
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
