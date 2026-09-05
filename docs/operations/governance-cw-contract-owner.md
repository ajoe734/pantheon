# Governance CW01/CW03/CW04 Contract Owner

Status: corrective restoration complete (BFF-GOVERNANCE-CW-CONTRACT-CORRECTIVE-PREREQUISITE-001)

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
