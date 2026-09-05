# Governance CW01/CW03/CW04 Contract Owner

Status: corrective restoration complete (BFF-GOVERNANCE-CW-CONTRACT-CORRECTIVE-PREREQUISITE-001);
read-port empty/missing-truth and fail-closed policy gaps closed
(BFF-CW-READ-POLICY-CLOSURE-PREREQUISITE-001)

## Single owner

`services/control-plane/bff/governance/service.py` (`GovernanceService`) is
the sole owner of CW01 consult-request validation, CW03 committee
list/detail projection and action-policy, and CW04 red-team memo
projection and governance-review gate. `services/control-plane/bff/governance/router.py`
(`create_governance_router`) is the only HTTP adapter for these surfaces.
`services/control-plane/bff/ports/operations_consultation.py`
(`DomainConsultationPort`) is the only place the public CW literals are
translated to/from the domain `ConsultationServiceClient`/`ConsultationStore`
enums, and the only place committee board rows are read from consultation
session records. `main.py` assembles these dependencies (via
`app_deps.read_surface`) and no longer holds a second copy of the CW03
projection/action-policy implementation.

## What this corrective restored

- **CW01** (`docs/bff/CW-01-consult-request.md`): the public
  `consultation_type` field now validates against the six published
  subtypes (`pre_deployment`, `risk_review`, `macro_regime_shift`,
  `incident_response`, `policy_change`, `general`) and `priority` against
  the four published values (`low`, `normal`, `high`, `critical`) — not the
  internal `ConsultRequestType`/`ConsultPriority` enum member names that had
  regressed into the public validation set. `context_refs[].type` is
  validated against the published ref-type set
  (`artifact`, `deployment_plan`, `incident`, `lineage_edge`,
  `telemetry_ref`, `note`). `DomainConsultationPort` restores the exact
  historical subtype→`ConsultRequestType` and priority→`ConsultPriority`
  mapping table (commit `aba0cd0087f297dadfff5769d5a97f4bdc3215e8`,
  `services/control-plane/bff/read_store.py:88-105` pre-extraction) in both
  the service-client and local-typed-store create branches, so a
  `risk_review`/`critical` request now actually reaches the domain as
  `EXECUTION_RISK`/`URGENT` instead of silently falling back to
  `STRATEGY_REVIEW`/`NORMAL`. `GovernanceService.create_consult_request` no
  longer synthesizes a `created`+UUID+`canCancel` response when the port
  returns nothing; a missing/failed provider now fails closed
  (`503 DEPENDENCY_UNAVAILABLE`). Detail/cancel responses are the published
  root envelope (not wrapped in `data`); list uses `data[]` with
  `meta.surfaces.consult_request_list`.
- **CW03** (`docs/bff/CW-03-committee-board.md`): `GovernanceService` now
  owns `committee_projection` (surface-state → allowed-actions →
  full projection), used by both the `GET /api/v1/committees/{id}` route
  and the `RecordSponsorDecision` command validator in `main.py`, so a GET
  read and the command's authorization decision can no longer diverge. The
  three former duplicate implementations in `main.py`
  (`_cw03_committee_surface_state`, `_cw03_allowed_actions`,
  `_cw03_committee_projection`) are removed; `main.py`'s
  `_validate_record_sponsor_decision` now builds a `GovernanceService`
  over the same `read_store` and delegates to `committee_projection`.
  `DomainConsultationPort.list_committees`/`get_committee` were added
  (they did not exist before — the pre-existing `ReadSurfacePorts`
  fallback silently called the wrong port method), backed by the same
  `_consultation_session_records()` projection used by consultation reads.
  List returns `data[]` (not `items[]`) with `meta.surfaces.committee_board`.
- **CW04** (`docs/bff/CW-04-redteam-memo.md`): `GovernanceService` now owns
  `consult_memo_projection`, including the `canInitiateGovernanceReview`
  gate (published/valid-target/authority/no-active-review/not
  suppressed-or-withdrawn/supported-target-type, forced `false` on
  degraded/unavailable surfaces) and evidence redaction via the same
  `redact_evidence_refs`/`capabilities_for_identity` callables the router
  already injected for other surfaces. This field previously did not exist
  anywhere in the response.

## BFF-CW-READ-POLICY-CLOSURE-PREREQUISITE-001: what this follow-up closed

The predecessor corrective above added real `list_committees`/`get_committee`
methods to `DomainConsultationPort`, but left the composed
`ReadSurfacePorts.list_committees`/`get_committee` wrapper's older
cross-domain fallback in place, and left the shared `ReadSurfacePorts.dataset_source`
default at a blanket `"typed_store"` for every consultation-owned dataset.
This follow-up closes those gaps without introducing a second implementation:

- **Read-port empty/missing truth**: `ReadSurfacePorts.list_committees`/
  `get_committee` (`ports/read_surface_ports.py`) now delegate directly to
  `operations_consultation.list_committees`/`get_committee` with no fallback.
  Previously, an empty `[]` from the real committee port was replaced with
  `list_workflow_templates()` (raising `TypeError` downstream on filter
  kwargs it doesn't accept), and a `None` from `get_committee` was replaced
  with an unrelated `get_consult_request` lookup by the same id — silently
  aliasing a committee to an unrelated consult request that happened to
  share an identifier. `InMemoryOperationsConsultationPort` (the typed
  local-store test double) gained the same committee-board projection
  methods so both the `service_client`/`service_store` branch and the
  in-memory typed-double branch expose real committee reads, not an
  absent-attribute gap that used to trigger the fallback.
- **Single availability policy, unavailable dominates**: `ReadSurfacePorts.dataset_source`
  routed every operations-consultation-owned dataset (`consult_requests`,
  `consult_memos`, `consult_rules`, `route_policies`, `workflow_templates`,
  and sibling catalog datasets) to a hardcoded `"typed_store"` default
  whenever `research_knowledge_source` didn't recognize the dataset name —
  even when the consultation client/store was genuinely absent. It now
  delegates those datasets to `operations_consultation.dataset_source(...)`,
  which truthfully reports `service_client`/`service_store`/`missing`.
  `GovernanceService._committee_surface_state` (`governance/service.py`)
  also only checked a committee record's own `surface_state` for
  `"degraded"`, silently ignoring an explicit `"unavailable"` record state
  whenever the dataset itself reported `"ok"`; it now checks `"unavailable"`
  first, so an explicit unavailable committee record always dominates a
  healthy dataset source (matching the CW04 memo path, which already did
  this correctly).
- **Redaction/capability fail-closed default**: `GovernanceService`'s
  built-in default `redact_evidence_refs` (used only when the router/main
  composition root does not inject the real
  `models.redact_evidence_refs`/`_capabilities_for_identity` pair — e.g. a
  bare `GovernanceService(store)` in a probe or future caller) unconditionally
  returned evidence unredacted. It is now a fail-closed default that
  withholds every evidence ref with `reason: "redaction_policy_unavailable"`
  rather than defaulting to open disclosure.
  `consult_memo_projection` also normalizes a `None`/failed
  `capabilities_for_identity` result to an explicit empty capability list
  before calling the real redactor, so a capability-lookup exception is
  gated the same as an authenticated identity with zero capabilities,
  instead of being treated as "capability policy not applicable" and
  passed through unredacted. Production wiring in `main.py`
  (`redact_evidence_refs=redact_evidence_refs`,
  `capabilities_for_identity=_capabilities_for_identity`) already supplies
  the real canonical policy and is unchanged; these fixes only close the
  fail-open default path.

Regression coverage for all three gaps lives in
`scripts/test_bff_cw_contract_prerequisite.py` alongside the predecessor's
CW01/CW03/CW04 tests.

## Known gap intentionally left untouched (not this corrective's scope)

`RecordSponsorDecision`'s command **execution** (persisting the sponsor
decision, as opposed to the read-projection/authorization check this
corrective converges) still calls `read_store.record_sponsor_decision(...)`
in `main.py`, and no port in the current composition implements that method
— this call fails today with `AttributeError` inside the async command
worker. This is a pre-existing write-path gap explicitly deferred to
`DOMAIN-WRITERS-001` per the signed SA/SD
(`GOVERNANCE_CW_CONTRACT_SA_SD.md`); this corrective only restores read
contracts and the existing command *validator*, not general write/approval
persistence.

## Existing BFF characterization tests

`services/control-plane/bff/test_cw03_committee_board_contract.py`,
`test_cw04_redteam_memo_contract.py`, `test_ask_004_memo_publish_contract.py`,
and several sibling `test_*_contract.py` files in the same directory use a
`sys.path`/`importlib` trick (`import main as bff_main`) to load
`services/control-plane/bff/main.py` as a bare top-level module. That trick
predates this task and fails independently of it —
`from .models import ...` inside `main.py` raises
`ImportError: attempted relative import with no known parent package`
whenever `main` is imported without its real package context
(`services.control_plane.bff.main`), which this corrective's regression
suite uses instead. `services/control-plane/bff/tests/test_governance_router.py`
(the read-only reference test explicitly named in this task's brief) does
import correctly and mostly passes; its
`test_consult_request_committee_memo_and_workbench_routes` case sends a
`consult_requests` create payload using the pre-regression internal enum
name (`consultation_type: "strategy_review"`) and the wrong context-ref key
names (`ref_type`/`ref_id` instead of `type`/`id`), so it now correctly
receives `422` against the restored published contract. Reconciling that
characterization test and the `import main` loader trick is
`BFF-TEST-ARCH-001`'s scope, not this corrective's.
