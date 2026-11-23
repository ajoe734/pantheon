# Persona Meeting Action Capability & Daily-Cycle Execution Contract

Task: `PERSONA-MEETING-ACTION-EXECUTION-20261123`
Source baseline inspected before editing: `bd47d73bc` (`origin/dev`).
Class: RC2 WP2.3, cross-reference RC6 meetings and `MEETING-HOSTED-ACCEPTANCE-20261115`.

## 1. Current Callers, Storage Owners & Test Surface Trace

- **Persona & Capability Snapshot Owners (`services/persona/write_owner.py`)**:
  - `PersistentPersonaOwner` owns durable Persona registry records (`GET/POST/PATCH /api/personas`, `/api/personas/{persona_id}`).
  - `PersistentCapabilitySnapshotOwner` owns immutable capability snapshot receipts (`PUT /api/personas/{persona_id}/capability-snapshots/{snapshot_id}`, `GET /api/personas/{persona_id}/capability-snapshot`, `GET /api/capability-snapshots/{snapshot_id}`).
  - Existing callers: `services/control-plane/persona/main.py` (`_capability_snapshot` for discovery), `services/consultation/meetings.py` (`_fetch_persona_directory`), `services/persona/test_persona_owner_read_roles.py`.
- **Effective Policy Resolver (`services/control-plane/persona/persona_policy_resolver.py`)**:
  - `PersonaPolicyResolver.resolve` / `resolve_to_snapshot` resolves `tool_profile_id`, `route_policy_id`, `consult_policy_id`, and capital binding eligibility into `PolicyResolutionResult` (`effective_tools`, `effective_skills`, `effective_workflows`, `denial_reasons`, `consultable`).
  - Existing callers: `services/control-plane/persona/main.py`, `services/control-plane/persona/test_persona_policy_resolver.py`.
- **Broker & Governed Agent Tool Profile (`services/openclaw-gateway-adapter/persona_tool_broker.py`, `integrations/openclaw/persona_agent_sync.py`, `integrations/openclaw/plugins/pantheon-persona-tools/`)**:
  - `PERSONA_AGENT_TOOL_PROFILE` grants only `read`, `persona_memory_recall`, `persona_mental_model_read`, and `persona_proposal_submit`.
  - `PersonaToolBroker` binds each run to a `purpose` (`daily_cycle` or `panel_interpretation`). `panel_interpretation` permits only `persona_memory_recall` on committee scope + facts and refuses `persona_proposal_submit`.
- **Daily Cycle Controller & OODA Turn Runtime (`services/persona/persona_cycle_controller.py`, `integrations/openclaw/persona_ooda_runtime.py`, `services/persona/case_file_outcomes.py`, `services/persona/meeting_triggers.py`)**:
  - `PersonaCycleController.consume_run` claims each terminal OpenClaw cron run in the OODA packet store (`ooda_loop_packets.jsonl`), runs `CycleOwners.observe`, composes the decision `CaseFile`, executes one brokered turn (`run_persona_ooda_turn`), reads proposals exclusively from the broker's per-run tool record, verifies citations, runs deterministic pool/lesson/meeting steps, and persists the closed packet.
  - `CaseFileOutcomes.reconcile` tracks cited `CaseFile` items across persisted packets on `persona.metadata["case_file_outcomes"]`.
  - `MeetingTriggers.run` evaluates deliberative meeting triggers (`MT-1..MT-6`) inside the daily cycle.

## 2. Per-Participant Capability Lookup Contract

`evaluate_participant_action_capability` (in `services/control-plane/persona/persona_policy_resolver.py`, re-exported by `services/persona/write_owner.py` and exposed at `GET /api/personas/{persona_id}/action-capability` and `POST /api/personas/action-capabilities`) joins:
1. Authoritative Persona record from `PersistentPersonaOwner` (`status`, `lifecycle_state`, `required_data_sources`, `tool_profile_id`, `route_policy_id`, `consult_policy_id`, `metadata`, `tenant_id`),
2. Authoritative capability snapshot from `PersistentCapabilitySnapshotOwner` + `PersonaPolicyResolver.resolve`,
3. Broker tool profile (`PERSONA_AGENT_TOOL_PROFILE` / `PersonaToolBroker._profile_allows`) and `run_purpose` (`daily_cycle` vs `panel_interpretation` / `consultation`),
4. Enrolled trading role (`lifecycle_state in {"paper_owner", "paper_running"}`, active, not panel/advisor/servant),
5. Accessible vs restricted data (`required_data_sources`, `policy_gates`, `blocked_source_scopes`, snapshot `restrictions`) against the specific deliverable requirements, and
6. Owner availability (`persona_owner`, `capability_owner`, `research_owner`, `registry_owner`).

Returned capability summary shape (safe for consultation pre-read, chair synthesis, BFF projection, and daily-cycle execution recheck):

```json
{
  "persona_id": "persona-alpha",
  "role": "chair",
  "meeting_role": "chair",
  "lifecycle_state": "paper_owner",
  "admin_status": "active",
  "status": "allowed",
  "executable": true,
  "can_be_assigned": true,
  "can_receive_persona_action": true,
  "can_execute_in_current_run": true,
  "can_sponsor_formal_proposal": true,
  "can_mutate_trading_state": false,
  "consultation_can_mutate_trading_state": false,
  "advisory_only": false,
  "blocked_reason": null,
  "reason": "Active persona with workflow and research capability",
  "denial_reasons": [],
  "run_purpose": "daily_cycle",
  "effective_tools": ["read", "persona_memory_recall", "persona_mental_model_read", "persona_proposal_submit"],
  "effective_workflows": ["persona_daily_cycle"],
  "effective_skills": [],
  "accessible_data": ["tw_equities_daily"],
  "restricted_data": [],
  "data_access_scope": "standard",
  "allowed_deliverables": ["research_note", "research_artifact"],
  "allowed_proposal_types": ["RESEARCH_TICKET", "EXPERIMENT_SPEC", "PARAMETER_CHANGE", "GOVERNANCE_SUGGESTION", "INCIDENT_ANNOTATION"],
  "allowed_execution_targets": ["research_ticket", "research_note", "research_artifact"],
  "limits": ["proposal_only_no_direct_trading", "paper_scope_only"],
  "snapshot_id": "snap-persona-alpha-1",
  "source_refs": ["persona_owner:persona-alpha"]
}
```

Covered statuses:
- `allowed`: active enrolled daily-cycle persona with valid workflow, decision tool, unexpired/undowngraded capability snapshot, and accessible data for the deliverable.
- `advisory_only`: panel personas (`persona-risk-officer`, `persona-red-team`, `consult_panel`), `agora_advisor`, `private_servant`, non-daily-cycle lifecycle (`consultable`, `research_only`, `draft`), or `run_purpose` restricted to consultation/panel interpretation.
- `suspended`: `status == "suspended"` / `"archived"` or `lifecycle_state in {"frozen", "retired"}`.
- `no_workflow`: missing/denied route policy or empty effective workflows / disabled workflow.
- `restricted_data`: deliverable requires a dataset/scope in `restricted_data` / `blocked_source_scopes`, ungranted `policy_gates`, or foreign/private scope.
- `expired_access` / `downgraded_access`: capability snapshot `expires_at` elapsed, `access_status` expired/downgraded, or `persona_proposal_submit` / execution capability revoked.
- `owner_unavailable`: Persona, CapabilitySnapshot, Research, or Registry owner temporarily unreachable/erroring.

Security invariants:
- Role labels (`chair`, `trading_peer`) and unverified inline BFF `capability_snapshot` fields never grant capability.
- `anonymize_participant_capability` strips `persona_id`, `snapshot_id`, and `source_refs` for anonymized meeting rounds.

## 3. Daily-Cycle Ticket Observation, Execution, Human-Input Request/Resumption & Same-Subject Citation

1. **Deterministic Observation (`CycleOwners.observe_meeting_actions`)**:
   - Reads `GET /api/research/tickets?meeting_linked=true&owner=<persona_id>&page_size=50` (up to 2 pages, filtered to `tenant_id == persona["tenant_id"]` and `owner == persona_id`).
   - Resolves approved evidence (`meeting_action.source_refs`, `human_input.evidence_refs`, `evidence_refs`) from `/api/research/artifacts/{id}`, `/api/research/notes/{id}`, and `/api/registry/entries/{id}` within the same tenant.
   - Populates `observe["meeting_actions"]` (`runnable` capped at 4 tickets/tick, `verified_results` capped at 4 tickets/tick) and injects deterministic `CaseFile` items into `facts`:
     - `facts:meeting_action_ticket:<ticket_id>`
     - `facts:meeting_action_result:<ticket_id>`
     - `facts:meeting_action_evidence:<ticket_id>:<artifact_id>`
2. **Execution via Existing Normal Decision Tool (`persona_proposal_submit`)**:
   - No 4th tool and no extra cycle. `CycleProposal.kind` accepts `"meeting_action"` with `parameters`:
     - `ticket_id` (or `action_id`), `action` (`"submit"` | `"request_input"` | `"decline"`), `title`, `analysis` / `body`, `artifact_refs` / `evidence_refs`, `missing_requirement`, `human_actor_id`.
   - For `action="submit"`, the controller rechecks `evaluate_participant_action_capability`, checks/creates a deterministic meeting-bound research note (`GET /api/research/notes/{note_id}` then `POST /api/research/notes` with `meeting_ticket_id` and `idempotency_key`), and submits via `PATCH /api/research/tickets/{ticket_id}` (`action="submit"`, `expected_revision`, `idempotency_key`, `evidence_refs`).
   - For `action="request_input"` (or deterministic missing external requirement), the controller patches the original ticket with `action="request_input"`, `reason`, and `human_actor_id`. Once an authorized human supplies input (`provide_input`), the ticket returns to `action_state="in_progress"` with `required_actor=persona_id` and `human_input`, and the same Persona resumes and submits on its existing daily cycle.
   - Timeouts, missing tool records, declined proposals, or rejected outputs never fabricate a submission.
3. **Same-Subject Next Decision & Citation**:
   - Same-subject verified results (`submitted` / `accepted`) and their resolved evidence appear in `CaseFile` (`facts:meeting_action_result:<ticket_id>`, `facts:meeting_action_evidence:<ticket_id>:<artifact_id>`), allowing the next decision (including `kind="no_action"` to maintain course with an explicit reason) to cite them even when generic memory search returns zero hits.
