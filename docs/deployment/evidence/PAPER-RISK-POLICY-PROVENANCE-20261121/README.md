# Paper risk policy provenance

Owner Codex2; reviewer Antigravity. The three trading personas have executable
inline policies, but their requested `low` profile has no proven approved
mapping to those financial limits. The hosted probe therefore **fails**, after
checking all three. A null reference is not the defect by itself. This task
changes evidence, the read-only probe and RC2/RC6 incident linkage only.

## Hosted identity and evidence

Reads were taken on 2026-11-21 at approximately 13:06–13:18 UTC. Hosted
`dev-root/acceptance/latest.json` reports backend
`b66d17afc05e72a7010369264ecf1ca8d7f9b9e6`, frontend
`9f3793aa7094d250d48b8f73f34ee10a52f6e2ce`, accepted release run
[44861265420](https://github.com/ajoe734/pantheon/actions/runs/44861265420)
at 04:59:29Z. BFF and paper-signal-producer image revision labels matched that
backend. This pre-existing release acceptance does not accept this risk chain.

- [owner-readback.json](owner-readback.json): three tenant-filtered durable
  provisioning requests, their capital/binding receipts and current BFF persona
  fields. The database transaction was read-only, with a 15 s statement cap;
  no store constructor/bootstrap was called.
- [runtime-readback.json](runtime-readback.json): runtime-manager desired state,
  the three current Capital owner pool records, the result of invoking the
  deployed `resolve_bound_pool_risk_policy`, frozen artifact content, checksums,
  replacement parent bindings and loader attestations. The reserved release
  probe persona `persona-d64835a203ce276b8fbb` is explicitly excluded.
- [policy-inventory.json](policy-inventory.json): 697 governance decisions read
  under the existing tenant-scoped risk-owner identity, target-type counts,
  no policy-mapping candidates, the two 2317 artifact-admission decisions,
  configured policy-directory absence and deployed source hashes. This is a
  bounded inventory of this tenant/configuration, not a claim about every
  possible external policy authority.
- [historical-events.json](historical-events.json) and [replay.json](replay.json):
  owner-accepted immutable events and a pure replay on the hosted producer.
- [evidence.json](evidence.json): review manifest, limits, validation and residual
  dispositions. Source hashes were compared with `git show b66d17afc:<path>`.

## Create, retry, binding and execution trace

All code references below refer to the served backend `b66d17afc`, rather than
an assumption that the latest dev code is hosted.

1. The cutover record
   [PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107](../PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107/cutover.md)
   identifies governed `POST /bff/personas` as the creation entry point. The
   authenticated caller was `pantheon-dev-admin-test`; all three durable caller
   payloads request `risk=low`, budget 100000, TWD, TW, daily paper momentum,
   and contain `risk_policy_ref=null`. Their complete normalized payloads are
   preserved in owner-readback. The original HTTP wire payload is not retained
   here, so omission versus explicit null cannot be distinguished from that
   record. No evidence supports blaming a frontend serializer for dropping a
   selected policy.
2. `personas/routes/collection.py::bff_create_persona` derives the tenant and
   caller, normalizes the payload and persists its request hash before the
   coordinator runs. `personas/service.py::_persona_create_canonical_payload`
   (6151–6184) copies the caller's payload, preserves an explicit snake-case
   reference and copies `riskPolicyRef` only if the snake-case field is absent.
   With neither reference supplied, the inserted value is null. It does **not**
   translate `risk=low` into a policy. Thus the earliest proven absent mapping
   is already in the durable request, not a later Capital or resolver discard.
   Explicit snake-case null also takes precedence over a camel-case value.
3. `persona_provisioning_coordinator.py::_coordinate_capital_pool`
   (1915–1990) passes that reference through and writes an independent
   `metadata.risk_policy`: `rp-<pool_id>`, version `1.0`, active, paper-only,
   equity/future, gross=net=leverage=2.0. These defaults originated in commit
   `ed3e8b625228806cd45184369ef444009b41c597`,
   `STRATEGY-ARTIFACT-VIEW-STRENGTH-POLICY-20261107`; the cutover record already
   records these exact policies on 11/08. They are code defaults, not a lookup
   of an approved `low` profile. `_create_then_get` (1860 onward) adopts an
   existing pool; retry/reconcile does not regenerate its policy. Pool readback
   validation checks identity/status/currency/single-runtime, not a low-profile
   mapping. Merely changing provisioning defaults would not repair these pools.
4. Capital owns the pool and PersonaCapitalBinding. `services/capital/main.py`
   preserves `risk_policy_ref` and metadata on create (299 onward).
   `CapitalRiskIncreaseGuard.authorize` (`capital_guard.py`, 205–226) returns
   for paper operations after checking a policy **only if a reference exists**.
   Hence these active paper pools pass Capital admission without referenced
   policy approval. This is distinct from runtime's ability to execute their
   inline policy. `main.py::pool_digest` (141–142) hashes pool identity,
   ownership, currency, budget, reference and single-runtime flag, **excluding
   metadata.risk_policy**. That digest alone does not approve financial limits.
5. Provisioning approves Registry strategy/spec entries with
   `risk_level=low`; `persona_provisioning_coordinator.py:2150` explicitly calls
   this `low: paper-only Persona admission, zero live capital`. The recorded
   2317 decisions target `registry_entry` at versions `1.0.0` and `1.2.0`, by
   `automated_gate` / `pantheon-dev-paper-provisioner`, scoped to dev paper with
   max live-capital scale zero. They do not approve a policy/profile mapping.
   Their expiry dates are historical admission expiry, not evidence of a valid
   current financial-policy approval or by themselves a requirement to revoke
   the already-running strategy. Renewal semantics belong to the binding task.
6. The three runtime IDs remain stable, but 11/18 pool publication replaced
   each original binding with an active envelope `v2` binding, artifact version
   `1.2.0`. Frozen artifacts embed the original approved momentum member at
   weight 1.0; the expression policy remains `notional_target_v1`, version
   `1.0`, full-strength fraction 1.0, leverage threshold 0.5, no pyramiding,
   close-before-reverse. The specific defaults are written in the provisioning
   coordinator's artifact builder (2563 onward), introduced by the same
   `ed3e8b625` source change; the served v2 object-store bytes and hashes are in
   runtime-readback. The loader attestation names the tenant, Capital pool,
   PersonaCapitalBinding, Registry approval and exact artifact checksum. It
   contains no financial-profile mapping. `runtime_manager/service.py`
   `_evaluate_risk_policy_for_deploy` (307–344) returns if no explicit policy
   payload was supplied. No financial policy is carried in these bindings.
7. The fleet `policy_envelope={stage:paper,fleet_eligible:true}` comes from
   `services/runtime-manager/fleet_desired_state.py::_policy_envelope_for`
   (327 onward); it is fleet admission, not financial limits. Likewise artifact
   `strategy_logic.risk_limits={gross_weight_limit:1,max_net_strength:1}` caps
   signal combination, not account gross exposure or single-name capital.
   Persona details still expose the original binding IDs, so the probe reads
   the runtime binding collection and selects the unique active tenant/persona
   match instead of following that stale display field.
8. `services/capital/risk_policy.py::resolve_bound_pool_risk_policy` gives an
   explicit pool reference precedence over inline policy. With a null reference
   it parses `metadata.risk_policy`; the deployed resolver returned the three
   v1.0 policies in runtime-readback. `PaperExecutionAlgorithm._resolve_active_risk_policy`
   (`paper_runtime.py`, 1092–1120) fails if resolution yields no active policy;
   it does not use the persona risk label. The production CapitalPoolReader
   is wired by `runtime_bootstrap.py:214–225`. Empty policy/whole-pool metadata
   must not be mistaken for the legitimate inline policy observed here.
9. `expression_inputs.py::build_expression_inputs` (334–371) reads the
   Capital budget or actual fill-backed ledger, gross exposure and reserved
   futures margin. It builds `gross_limit = policy.gross_limit * equity` and
   available cash capped by free margin. The expression policy selects the
   instrument and units from that budget. `allocate_targets` (615 onward)
   additionally allocates strength × full-strength fraction × gross budget for
   `target_position_v1`; these current artifacts still declare
   `notional_target_v1`, and the historical replay uses `decide`, not a newly
   manufactured target-position input. Both paths need the subsequent sizing
   repair to consume all applicable governed limits consistently.
10. `paper_runtime.py` resolves policy before submission, includes commission in
    the cash check, evaluates post-order gross/net/leverage and optional
    underlying weights (1545–1586), then submits to the paper broker only if
    allowed. The 2317 event proves this gate refused the order before broker
    submission. Fixing commission sizing does not approve a low profile or add
    a concentration/reserve policy.

## Three-persona readback

Every row requests `low`; budget is TWD 100000. Policy origin is inline,
version `1.0`, gross/net/leverage each `2.0` multiples of equity. IDs are exact;
full member/artifact checksums and original bindings are in runtime-readback.

| Persona | Capital pool / inline policy ID | Active runtime / binding | Active execution artifact |
|---|---|---|---|
| 青禾 / `persona-2448c7053e33ea48da47` | `pool-persona-paper-06d4775a9b6a8f63b311` / `rp-pool-persona-paper-06d4775a9b6a8f63b311` | `rt-ad81501b` / `rb-eb74118dd3ca4b169496b1c239087512` | `artifact-persona-pool-06d4775a9b6a8f63b311-v2` @ `1.2.0` |
| 澄川 / `persona-6507750312870d03a241` | `pool-persona-paper-039ad01f8bcd2b4aa466` / `rp-pool-persona-paper-039ad01f8bcd2b4aa466` | `rt-3252fa75` / `rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4` | `artifact-persona-pool-039ad01f8bcd2b4aa466-v2` @ `1.2.0` |
| 衡岳 / `persona-ee7112a829d38b9135ac` | `pool-persona-paper-a81b4fd15944698341aa` / `rp-pool-persona-paper-a81b4fd15944698341aa` | `rt-3b65a843` / `rb-2b0df30559154a93902c209b0318d95c` | `artifact-persona-pool-a81b4fd15944698341aa-v2` @ `1.2.0` |

For all three, `max_single_name_weight` is **absent**, normalized to `None`;
this dimension is not checked. It is not an explicit zero cap. There is also
**no strategic cash-reserve setting** in the pool policies, artifacts or current
RiskPolicy model; no percentage can be inferred. The historical replay's
`margin_reserved=0.0` is an explicit zero of actual futures margin, applicable
to free-margin arithmetic. Cash required for purchases includes commission.
Those are different meanings of reserve. Existing drawdown-baseline approvals
([successor baseline policy](../PAPER-SUCCESSOR-BASELINES-20261108/policy.md))
are evolution-monitoring thresholds, not an approved low sizing profile.

## Exact 2317 incident and replay

Source event `91d55953-b575-4fe8-8d39-6e0cade58d59`, journey
`tj_79726064-4cc2-5510-8e11-7f5e59ff89bd`, at 2026-11-19 23:46:17Z:

| Quantity | Recorded source | Same-input replay on b66d17afc |
|---|---|---|
| Capital / available cash | 100000 / 100000 TWD | unchanged |
| Gross budget | 200000 TWD | unchanged |
| Direction / strength | LONG / 0.91324200913242 | unchanged |
| Requested target | 182648.401826484 TWD | unchanged |
| Cash price / price date | 221 / 2026-11-18 | unchanged |
| Units | 452 | 451 |
| Fee-inclusive required cash | 100034.3461 | 99813.031175 |
| Remaining cash | no fill; order rejected | 186.968825 (186.97 rounded) |
| Broker submitted | false | no submission; pure replay |

The source includes the complete view, both candidates, book, budget and
parameters; replay does not reconstruct them from today's book. Its exact
expression target is still above the cash budget after #7657: commission-aware
sizing chooses 451 shares, not a newly approved risk allocation. Notional is
99671, or 99.671% of pre-fee equity; the fee-inclusive cash spend is 99.813031175%.
No strategic reserve or single-name cap was applied. The second captured event
`48c946c8-39d1-52bd-b1a1-ce72dc7f6e14` is for the existing RC6 history/cause
owners; a failed runtime-health evaluation alone cannot establish a booking
variance.

## Probe and reproduction

From a provisioned task checkout, run:

```bash
.venv-pantheon/bin/python3 -m pytest -q acceptance/test_paper_risk_policy_binding.py
timeout 185s acceptance/probes/paper-risk-policy-binding
```

The probe uses `ACCEPTANCE_BFF_URL`, `ACCEPTANCE_VIEWER_CLIENT_ID` and
`ACCEPTANCE_VIEWER_CLIENT_SECRET`. It performs GET only after token exchange.
The current viewer can read the three persona/pool/binding surfaces but the
approval endpoint returns 403. Optional `ACCEPTANCE_POLICY_READER_CLIENT_ID`
and `ACCEPTANCE_POLICY_READER_CLIENT_SECRET` use the existing read-authorized
identity only for the approval GET; runtime/persona/pool reads retain the
viewer. The second hosted run used the existing risk-owner identity and read
all 697 decisions; it still failed all three mappings. No credential/token is
printed or stored in evidence. Both terminal exit codes were 1, deliberately;
neither is reported as a passing acceptance check.

The probe pins the current three expected IDs. `ACCEPTANCE_EXPECTED_PERSONA_IDS`
can explicitly replace the comma-separated set; an empty set, duplicate IDs,
zero checked traders, missing/ambiguous binding, foreign tenant, missing or
invalid effective policy and unresolved mapping fail closed. A reference cannot
fall back to nearby inline data when its owner readback is unavailable. The
public BFF currently lacks that reference readback; the CLI fails closed for
references, and the function's injected owner-loader positive test is synthetic.

The mapping verifier looks for explicit financial-policy identity/version/content
and requested profile in an existing Capital activation approval subject. It
does not count a registry admission rating or the metadata-excluding pool digest
as such proof. **No such approved mapping was found.** Its positive fixtures
describe sufficient audit evidence only; they do not establish a new product
mapping schema, create an approval, or prescribe financial values. The next
owner must use the approved authority's actual mapping contract and update the
readback adapter if necessary, rather than activating these fixture values.

Supporting collectors are included for audit reproduction. Run each through
`timeout 180s ssh ... 'docker exec -i <container> python3 -' < <script>`:
`collect_provisioning.py` and `collect_approvals.py` in `pantheon-operator-bff-1`,
`collect_runtime.py` and `replay_hosted.py` in `pantheon-paper-signal-producer-1`,
and `collect_policy_files.py` in `pantheon-capital-1`. Resolve hosted identity
from the current deployment runbook before connecting. Collector stdout can be
larger than the committed selected evidence; do not commit whole owner stores.

Budget: on demand or the existing advisory post-switch runner; maximum 10
expected personas, 24 requests including login, 8 MiB per response, 2000 rows
per owner list, 15 s per HTTP read and 180 s total deadline. Current hosted scale
is three personas and 697 decisions. There is no new scheduler, emitter, cache,
table or persistent hosted writer, so added hosted storage/day is zero. Static
task evidence is committed once; collectors write stdout only, tokens stay in
memory, and disposable local captures can be removed after reviewed publication.
No new dev blocking gate is added.

## Evidence compaction after review

The three bulky readbacks retain every JSON value and hierarchy; containers at
nesting depth four use compact JSON formatting. `historical-events.json` is
353 lines, `owner-readback.json` 266, and `runtime-readback.json` 294, each below
the 400 added-line evidence cap. Use `python3 -m json.tool <path>` for expanded
local inspection. No hosted read was repeated and the original observation
times still apply.

The `evidence_compaction` section in `evidence.json` records SHA-256 digests
for original bytes, compacted bytes and canonical JSON content, plus line
counts. Original expanded bytes remain available at commit
`4209dbe755f38269d5fb4e46022ec3c837487c94` using the recorded repository paths.
Parsed equality against that commit was checked for all three files, so the
same source events, tenant owner receipts, inline policies, active bindings
and frozen artifacts remain available to the next task.

## CI return and repair

PR #7719 was returned from head `aff0c4f874ba` after required CI run
[44909662859](https://github.com/ajoe734/pantheon/actions/runs/44909662859)
failed. Shard 11's acceptance-stage regression hardcoded the previous probe
list; this task's new executable was correctly discovered but absent from that
expectation. The repair adds it and explicitly asserts `skipped` / exit 77
without credentials. The stage and policy-probe tests pass: 43 in 1.34 s.
Neither the production stage nor the financial-policy audit was changed.

Shard 10 also exposed an unrelated BFF parity-test clock dependency:
`test_dataset_surface_status_full_app_parity[unavailable]` compared separate
live timestamps of `13:39:40Z` and `13:39:41Z`
([job log](https://github.com/ajoe734/pantheon/actions/runs/44909662859/job/136862690396)).
The unchanged full BFF composition file passed 57 tests in 43.38 s locally.
This remains a residual finding for the coordinator to attach to RC1's
existing required-test clock-determinism class; a passing rerun does not fix
the dependency. No BFF test or runtime files were changed. The aggregate
Service suites failure follows the two failed shards. New head review is
required; the earlier approval is not reused.

## Handoff and residual dispositions

After #7718 merged at `20ad89fe3cddc6c1426ea3b139a9de3c94a1fc7c`,
composition anchor `635a9120473f6ffe13fe396e170b4751847b5834` resolved
the shared probe-list assertion with both `meeting-review-disposition` and
`paper-risk-policy-binding`. It retains the risk probe's skip/77 check,
persona-day exclusion and original release verdict check. The combined stage,
policy and meeting probe tests passed 47 tests in 5.74 s; the unchanged BFF
composition file passed 57 in 44.14 s. The RC1 live-clock residual still applies.
Human/Ops admitted the focused stage-test path to this original task's artifact
contract and reopened it; the earlier manifest admission hold is historical.
The final published head needs fresh required CI and Antigravity review.

The existing Capital/risk policy owner must decide whether `low` maps to an
existing approved profile/version or whether these exact inline policies are
explicitly applicable, and which optional single-name/reserve dimensions that
approved policy defines. Supply the tenant, approval, effective version and
financial contents to `PAPER-RISK-POLICY-BINDING-20261121`. Do not invent a
percentage or interpret missing as zero. That task owns creation/retry/existing
pool repair/rebinding/renewal. `PAPER-SIZING-BOUND-RISK-POLICY-20261121` then owns
restrictive sizing and final current-exposure validation; this replay is its
unchanged 451-share baseline, not its accepted result.

Residual findings are explicitly attached in the problem register and
evidence manifest: stale displayed original binding (RC2 persona-loop/binding),
absent mapping and metadata-excluding approval digest (RC2 binding), historical
admission expiry/renewal semantics (RC2 binding), and source-versus-projection
rejection/health-only reconciliation causes (RC6 cause/history chain). The
seven-task packet extends existing #7657/#7658/#7671/#7702 and execute-plans
#1052; no duplicate symptom task is opened. End-to-end hosted proof belongs to
`PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121` after its declared dependencies.
