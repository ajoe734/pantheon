#!/usr/bin/env python3
"""One-shot dispatcher for the 2026-05-28 BFF write-gap closure tasks.

Source FE spec: execute-plans/.lovable/specs/be-requirements/BE_WRITE_GAP_SPEC_2026-05-28.md
BE-view spec:   docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md

15 write endpoints on lupin dev BFF are 404 / 405 / 410 (Lovable 2026-05-28 probes).
This dispatcher emits 17 tasks across 5 EPICs:

  EPIC-WRITE-GAP-P0-LIFECYCLE   (4) - persona/pool/runtime actions + confirm endpoint
  EPIC-WRITE-GAP-P0-WIZARD      (4) - bindings/plans/approval-decisions + persona-mgmt health
  EPIC-WRITE-GAP-P1-AGORA       (6) - runtime create + 5 agora writes
  EPIC-WRITE-GAP-P2-MISC        (2) - v5 batch decide + Sentinel rule coverage
  EPIC-WRITE-GAP-OPS            (1) - redeploy lupin dev BFF + 15-route live curl

Owner / reviewer split (matches 2026-05-24 delta 3-class pattern):
  P0 + P2 + OPS -> Codex  / Claude
  P1 Agora batch-> Codex2 / Claude2

Babysit rule (feedback_babysit_deploy_tasks): do not mark OPS task done until
all 15 routes return Pack D-shaped 2xx or typed 4xx on live BFF.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"

SPEC = "docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md"
FE_SPEC = "execute-plans/.lovable/specs/be-requirements/BE_WRITE_GAP_SPEC_2026-05-28.md"
BE_BFF = "services/control-plane/bff/main.py"
BE_CATALOG = "services/control-plane/bff/action_catalog.py"
BE_EXEC = "services/control-plane/bff/command_executor.py"
FE_MGMT = "execute-plans/src/lib/bff-v1/management.ts"
FE_ONBOARD = "execute-plans/src/management/pages/PersonaOnboarding.tsx"
EVIDENCE_DIR = "support/evidence/bff-write-gap-20260528"
TEST_FILE = "services/control-plane/bff/test_bff_write_gap_2026_05_28.py"

PHASE_LIFE = "Sprint BFF-WRITE-GAP / EPIC-WRITE-GAP-P0-LIFECYCLE"
PHASE_WIZ = "Sprint BFF-WRITE-GAP / EPIC-WRITE-GAP-P0-WIZARD"
PHASE_AGORA = "Sprint BFF-WRITE-GAP / EPIC-WRITE-GAP-P1-AGORA"
PHASE_MISC = "Sprint BFF-WRITE-GAP / EPIC-WRITE-GAP-P2-MISC"
PHASE_OPS = "Sprint BFF-WRITE-GAP / EPIC-WRITE-GAP-OPS"

SPRINT_ID = "2026-05-28-pantheon-bff-write-gap"
SPRINT_OBJECTIVE = (
    "Pantheon BFF Write-Gap closure - 15 write endpoints on lupin dev BFF return "
    "404/405/410 against Lovable FE probes 2026-05-28 (probe-bff-write-paths + "
    "probe-persona-onboarding-endpoints). Frontend currently masks the gap with "
    "withWriteFallback 30-min overlay plus LiveStatusBanner degraded strip but "
    "Persona Onboarding Wizard cannot drive draft->active and every HighRiskConfirm "
    "2-step (retire promote_live runtime-start break-glass force-transition) stops "
    "at the confirm dialog. Five EPICs cover: P0-LIFECYCLE (4 task - register "
    "AdvanceLifecycle ApprovePool StartRuntime in action_catalog plus brand-new "
    "POST /bff/command-confirmations/{token}/confirm); P0-WIZARD (4 task - add POST "
    "methods to existing GET-only /api/v1/bindings /deployment-plans "
    "/approval-decisions plus data.health field on persona-management/{id}); P1-AGORA "
    "(6 task - POST /bff/runtimes plus 5 Agora writes); P2-MISC (2 task - v5 "
    "batch-decide plus Sentinel rule coverage for 6 HealthReasonCode); OPS (1 task - "
    "redeploy lupin dev BFF and live-curl-verify all 15 routes per babysit rule). "
    "Owner split matches 2026-05-24 delta 3-class pattern: P0 plus P2 plus OPS go "
    "Codex/Claude; P1 Agora batch goes Codex2/Claude2. Spec: "
    "docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md. Upstream FE "
    "spec: execute-plans/.lovable/specs/be-requirements/BE_WRITE_GAP_SPEC_2026-05-28.md. "
    "fail-closed rules unchanged; production live broker / capital binding still gated."
)

# (task_id, title, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    # ---------- EPIC-WRITE-GAP-P0-LIFECYCLE ----------
    (
        "BFF-WRITE-P0-LIFECYCLE-001",
        "POST /bff/personas/{id}/actions/AdvanceLifecycle (register in action_catalog)",
        "Codex", "Claude", PHASE_LIFE,
        "",
        # NOTE: parse_csv_env splits on ',' - avoid commas inside acceptance text.
        "Register action_id AdvanceLifecycle in services/control-plane/bff/action_catalog.py with required_roles persona_operator plus live_owner_approver (latter when target=live);"
        "Handler in command_executor.py validates target_state in paper_owner live_owner retired and confirm_token then drives persona lifecycle transition;"
        "Generic route /bff/personas/{persona_id}/actions/{action_id} at main.py:34350 dispatches via catalog so no new path registration needed;"
        "Response is 202 with data.commandId and data.status accepted plus data.from_state plus data.to_state;"
        "Error 412 CONFIRM_TOKEN_INVALID; 409 STATE_TRANSITION_INVALID on skip transitions; 403 INSUFFICIENT_PERMISSIONS;"
        "SSE publishes on personas:{id} plus management.persona-fleet plus audit:persona-{id};"
        "Audit chain appends persona.lifecycle.advance with prevHash plus hash per Pack D D26;"
        "pytest test_bff_write_gap_2026_05_28.py case advance_lifecycle covers happy path plus invalid target plus skip transition;"
        "Live verification after OPS task: curl POST /bff/personas/{dev_persona_id}/actions/AdvanceLifecycle returns 202",
        f"{SPEC};{FE_SPEC};{BE_BFF};{BE_CATALOG};{BE_EXEC};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P0-LIFECYCLE-002",
        "POST /bff/capital-pools/{id}/actions/ApprovePool (register in action_catalog)",
        "Codex", "Claude", PHASE_LIFE,
        "",
        "Register action_id ApprovePool in action_catalog.py with required_roles treasury_approver;"
        "Handler validates memo length at least 8 chars and confirm_token then transitions pool draft -> approved one-way;"
        "Generic route /bff/capital-pools/{pool_id}/actions/{action_id} at main.py:20795 dispatches via catalog;"
        "Response 202 with data.commandId plus data.pool_id plus data.state=approved;"
        "Error 422 MEMO_REQUIRED when memo missing or too short; 409 STATE_TRANSITION_INVALID when already approved; 403 FORBIDDEN;"
        "SSE publishes on capital-pools:{id} plus management.capital-pools;"
        "Audit chain appends capital_pool.approve;"
        "pytest case approve_pool covers happy path plus memo too short plus already approved;"
        "Live verification after OPS task: GET /bff/capital-pools/{pool_id} shows state=approved within 2s",
        f"{SPEC};{FE_SPEC};{BE_BFF};{BE_CATALOG};{BE_EXEC};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P0-LIFECYCLE-003",
        "POST /bff/runtimes/{id}/actions/StartRuntime (register in action_catalog)",
        "Codex", "Claude", PHASE_LIFE,
        "",
        "Register action_id StartRuntime in action_catalog.py with required_roles runtime_operator plus live_owner_approver (latter when runtime_kind=live);"
        "Handler validates confirm_token plus two_man_token (the latter required for live) then transitions stopped -> starting and triggers runtime daemon;"
        "Generic route /bff/runtimes/{runtime_id}/actions/{action_id} at main.py:37791 dispatches via catalog;"
        "Response 202 with data.commandId plus data.runtime_id plus data.state=starting;"
        "Error 403 TWO_MAN_REQUIRED when live without two_man_token; 412 CONFIRM_TOKEN_EXPIRED; 423 COOLDOWN_ACTIVE; 409 STATE_TRANSITION_INVALID;"
        "SSE publishes on runtimes:{id} plus management.runtime-status;"
        "Audit chain appends runtime.start;"
        "pytest case start_runtime covers paper happy path plus live two-man enforcement plus cooldown;"
        "Live verification after OPS task: SSE runtime.status=running within 30s per Pack D uiBudgets.runtimeStart",
        f"{SPEC};{FE_SPEC};{BE_BFF};{BE_CATALOG};{BE_EXEC};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P0-LIFECYCLE-004",
        "POST /bff/command-confirmations/{token}/confirm (top-priority - blocks every high-risk write)",
        "Codex", "Claude", PHASE_LIFE,
        "",
        "Register new route POST /bff/command-confirmations/{token}/confirm in main.py near the existing GET handler (grep command-confirmations);"
        "Body schema confirm_token plus command_id plus optional memo plus optional two_man_token;"
        "Looks up pending_confirmation command by token then promotes to accepted and triggers underlying action via command_executor;"
        "Response 202 with data.commandId plus data.confirmed_at;"
        "Error 404 when token unknown; 410 CONFIRM_TOKEN_EXPIRED past TTL; 412 CONFIRM_TOKEN_INVALID on hash mismatch; 403 TWO_MAN_REQUIRED when underlying action needs it and token absent;"
        "Audit chain appends command.confirm;"
        "SSE at minimum publishes audit:command-{commandId} plus whatever channel the underlying action specifies;"
        "Permission inherits from original action plus MFA enforced when required;"
        "pytest case confirm_command covers happy path plus expired token plus unknown token plus two-man path;"
        "Live verification after OPS task: POST /bff/command-confirmations/token-dev/confirm returns typed 4xx (NOT RESOURCE_NOT_FOUND with literal Not Found); valid live token returns 202;"
        "Blocks-every-high-risk-write rationale: HighRiskConfirm.tsx in FE wires every retire promote_live force-transition break-glass through this token endpoint",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),

    # ---------- EPIC-WRITE-GAP-P0-WIZARD ----------
    (
        "BFF-WRITE-P0-WIZARD-005",
        "POST /api/v1/bindings (method add - GET already exists at main.py:12744)",
        "Codex", "Claude", PHASE_WIZ,
        "",
        "Add POST handler alongside existing GET at services/control-plane/bff/main.py:12744 area;"
        "Body schema persona_id plus capital_pool_id plus role (paper_owner|live_owner) plus allowed_deployment_scope plus budget plus optional expires_at;"
        "Validates pool is approved plus persona lifecycle compatible with role;"
        "Persists binding entity returning 201 with id plus created_at;"
        "Error 409 CONFLICT when binding for persona+pool already exists; 422 VALIDATION_FAILED when pool not approved or persona lifecycle mismatch; 403 FORBIDDEN;"
        "SSE publishes on bindings:{persona_id} plus personas:{persona_id};"
        "Audit chain appends binding.create;"
        "Permission persona_operator plus binding role implied permission;"
        "Persona readiness derives binding=done so subsequent GET persona-management includes new binding in bindings[];"
        "pytest case create_binding covers happy path plus duplicate plus unapproved pool;"
        "Live verification after OPS task: Probe stage 2c returns 201",
        f"{SPEC};{FE_SPEC};{BE_BFF};{FE_ONBOARD};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P0-WIZARD-006",
        "POST /api/v1/deployment-plans (method add - GET at main.py:12774)",
        "Codex", "Claude", PHASE_WIZ,
        "",
        "Add POST alongside existing GET at main.py:12774 area;"
        "Body schema binding_id plus artifact_id plus deployment_mode (paper|live) plus capital_pool_id plus optional params plus optional locked;"
        "Validates artifact is approved plus binding is active;"
        "Returns 201 with id plus status pending_approval plus created_at;"
        "Error 409 when artifact not approved; 422 on schema fail; 403;"
        "SSE publishes on deployment-plans:{id} plus personas:{persona_id};"
        "Audit chain appends deployment_plan.create;"
        "Permission persona_operator;"
        "Persona readiness derives plan=done;"
        "pytest case create_deployment_plan covers happy path plus unapproved artifact plus locked flag;"
        "Live verification after OPS task: Probe stage 3 returns 201",
        f"{SPEC};{FE_SPEC};{BE_BFF};{FE_ONBOARD};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P0-WIZARD-007",
        "POST /api/v1/approval-decisions (method add - GET at main.py:12800)",
        "Codex", "Claude", PHASE_WIZ,
        "",
        "Add POST alongside existing GET at main.py:12800 area;"
        "Body schema plan_id plus decision (approve|reject) plus memo (>=8 chars) plus optional two_man_token;"
        "Validates approver is in plan reviewerQuorum per Pack D plus two-man enforced when plan.deployment_mode=live;"
        "Returns 202 with data.commandId plus data.plan_id plus data.decision plus data.approver_id plus data.decided_at;"
        "Error 403 INSUFFICIENT_PERMISSIONS when not in quorum; 403 TWO_MAN_REQUIRED when live without two_man_token; 422 MEMO_REQUIRED; 409 when already decided;"
        "SSE publishes on approvals:{plan_id} plus deployment-plans:{plan_id} plus personas:{persona_id};"
        "Audit chain appends approval.decide;"
        "On approve sets plan status approved and persona readiness approval=done;"
        "pytest case create_approval_decision covers approve plus reject plus already-decided plus quorum-miss plus live two-man;"
        "Live verification after OPS task: Probe stage 4 returns 202 and plan status flips within 2s",
        f"{SPEC};{FE_SPEC};{BE_BFF};{FE_ONBOARD};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P0-WIZARD-008",
        "GET /api/v1/operator/persona-management/{id} - add data.health field",
        "Codex", "Claude", PHASE_WIZ,
        "",
        "Extend response envelope at services/control-plane/bff/main.py:17225 to add top-level key data.health;"
        "data.health shape status (healthy|degraded|critical) plus score 0..100 plus reasons array of HealthReasonCode;"
        "HealthReasonCode enum persona_lifecycle_not_active plus no_runtime_binding plus active_incident plus drawdown_threshold plus negative_pnl plus runtime_status_attention;"
        "Reuse existing health derivation from persona-fleet (grep persona-fleet for current scorer) so data.health value matches persona-fleet[].health for the same persona id;"
        "Existing keys persona plus bindings plus deploymentPlans plus approvals plus runtimeBindings unchanged;"
        "Also fix the 404 for dev personas listed in probe (FE expects 200 for personas that exist in persona-fleet);"
        "Error 404 only when persona id is truly missing; 403 INSUFFICIENT_PERMISSIONS for non-operator;"
        "pytest case persona_management_detail covers all six top-level keys plus health parity with persona-fleet;"
        "Live verification after OPS task: Probe F4 returns 200 with data.health defined for at least 1 persona",
        f"{SPEC};{FE_SPEC};{BE_BFF};{FE_MGMT};{TEST_FILE}",
    ),

    # ---------- EPIC-WRITE-GAP-P1-AGORA ----------
    (
        "BFF-WRITE-P1-AGORA-009",
        "POST /bff/runtimes (method add - GET only exists)",
        "Codex2", "Claude2", PHASE_AGORA,
        "",
        "Add POST handler at services/control-plane/bff/main.py for /bff/runtimes;"
        "Body schema name plus persona_id plus binding_id plus deployment_plan_id plus runtime_kind (paper|live) plus optional params;"
        "Validates binding does not already have a runtime plus deployment_plan is approved;"
        "Returns 201 with id plus name plus state=stopped plus created_at;"
        "Error 409 CONFLICT when binding already has runtime; 422 on validation fail; 403;"
        "SSE publishes on runtimes:{id} plus management.runtime-status;"
        "Audit chain appends runtime.create;"
        "Permission runtime_operator;"
        "After this lands FE management-agent create_runtime tool is re-enabled (FE follow-up not in scope);"
        "pytest case create_runtime covers happy path plus duplicate binding plus unapproved plan;"
        "Live verification after OPS task: probe row returns 201",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P1-AGORA-010",
        "POST /bff/agora/signals (method add - GET at main.py:19006)",
        "Codex2", "Claude2", PHASE_AGORA,
        "",
        "Add POST alongside existing GET at main.py:19006 area;"
        "Body schema title plus body plus optional market plus optional tags plus optional linkedPersonaIds plus optional linkedStrategyIds plus optional severity (info|warn|alert);"
        "Returns 201 with id plus status=open plus createdAt;"
        "Error 422 on schema fail; 403 when caller below analyst role;"
        "SSE publishes on agora.signals plus agora.inbox;"
        "Audit chain appends agora.signal.create;"
        "Permission analyst or above;"
        "pytest case create_agora_signal covers happy path plus missing title plus role gate;"
        "Live verification after OPS task: probe row returns 201",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P1-AGORA-011",
        "POST /bff/agora/feedback (new route - distinct from per-signal feedback at main.py:19054)",
        "Codex2", "Claude2", PHASE_AGORA,
        "",
        "Register new POST /bff/agora/feedback at main.py (note: existing /bff/agora/signals/{signalId}/feedback at line 19054 is per-signal; this is the canonical bulk write; worker decides between alias and standalone handler but acceptance only requires the path responds 201);"
        "Body schema signal_id plus verdict (useful|noise|false_positive) plus optional memo;"
        "Returns 201 with id plus signal_id plus verdict plus author_id plus created_at;"
        "Error 404 RESOURCE_NOT_FOUND when signal_id unknown; 422 on bad verdict; 403;"
        "SSE publishes on agora.signals:{signal_id};"
        "Audit chain appends agora.feedback.create;"
        "Permission analyst;"
        "pytest case create_agora_feedback covers happy path plus unknown signal;"
        "Live verification after OPS task: probe row returns 201",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P1-AGORA-012",
        "POST /bff/agora/inbox/{id}/triage (new route)",
        "Codex2", "Claude2", PHASE_AGORA,
        "",
        "Register new POST /bff/agora/inbox/{id}/triage at main.py;"
        "Body schema disposition (ack|snooze|dismiss|escalate) plus optional memo plus optional snooze_until ISO8601;"
        "Returns 202 with data.commandId plus data.inbox_id plus data.disposition;"
        "Error 404 RESOURCE_NOT_FOUND when inbox id unknown; 422 when disposition snooze without snooze_until; 403;"
        "SSE publishes on agora.inbox;"
        "Audit chain appends agora.inbox.triage;"
        "Permission analyst;"
        "pytest case triage_inbox covers ack plus snooze plus dismiss plus escalate plus invalid disposition;"
        "Live verification after OPS task: probe row returns 202",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P1-AGORA-013",
        "POST /bff/agora/skill-coaching (new route)",
        "Codex2", "Claude2", PHASE_AGORA,
        "",
        "Register new POST /bff/agora/skill-coaching at main.py;"
        "Body schema skill_id plus optional persona_id plus prompt plus optional expected_behavior plus optional examples array of objects;"
        "Returns 201 with id plus skill_id plus status=queued;"
        "Error 422 on bad schema; 403 when caller is neither coach nor analyst;"
        "SSE publishes on agora.skill-coaching;"
        "Audit chain appends agora.skill_coaching.create;"
        "Permission coach or analyst;"
        "pytest case create_skill_coaching covers happy path plus missing prompt plus role gate;"
        "Live verification after OPS task: probe row returns 201",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),
    (
        "BFF-WRITE-P1-AGORA-014",
        "POST /bff/agora/postmortems (method add)",
        "Codex2", "Claude2", PHASE_AGORA,
        "",
        "Add POST /bff/agora/postmortems at main.py (no existing GET found - register fresh);"
        "Body schema optional incident_id plus title plus body plus root_cause plus action_items array of objects with owner due description;"
        "Returns 201 with id plus title plus status=draft plus created_at;"
        "Error 422 when action_items missing or owner empty; 403;"
        "SSE publishes on agora.postmortems;"
        "Audit chain appends agora.postmortem.create;"
        "Permission analyst;"
        "pytest case create_postmortem covers happy path plus empty action_items plus role gate;"
        "Live verification after OPS task: probe row returns 201",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),

    # ---------- EPIC-WRITE-GAP-P2-MISC ----------
    (
        "BFF-WRITE-P2-MISC-015",
        "POST /bff/v5/interventions/batch-decide (new route)",
        "Codex", "Claude", PHASE_MISC,
        "",
        "Register new POST /bff/v5/interventions/batch-decide at main.py;"
        "Body schema items array (max 50) of intervention_id plus decision (approve|reject) plus memo (>=8 chars) plus optional two_man_token;"
        "Loops single-decide handler per item collecting per-item commandId plus status;"
        "Returns 202 with data.batchId plus data.accepted count plus data.rejected count plus data.items array of intervention_id plus commandId plus status;"
        "Error 403 INSUFFICIENT_PERMISSIONS; 403 TWO_MAN_REQUIRED when any item is live without two_man_token; 422 VALIDATION_FAILED when items > 50 or any memo < 8 chars;"
        "SSE publishes one event per item on v5.interventions;"
        "Audit chain appends v5.intervention.batch_decide (single batch entry with array of per-item hashes);"
        "Permission same as single decide (operator|approver|admin);"
        "pytest case batch_decide_interventions covers happy path plus oversize batch plus memo too short plus mixed approve-reject;"
        "Live verification after OPS task: probe row returns 202 with item-count matching submission",
        f"{SPEC};{FE_SPEC};{BE_BFF};{TEST_FILE}",
    ),
    (
        "SENTINEL-RULE-COVERAGE-HEALTHREASON-001",
        "Add Sentinel rules covering 6 HealthReasonCode values (rule engine work; not an endpoint)",
        "Codex", "Claude", PHASE_MISC,
        "",
        "Locate Sentinel rule registry (grep services/sentinel/ for register_rule or sentinel_rules table);"
        "Add one rule per HealthReasonCode: persona_lifecycle_not_active plus no_runtime_binding plus active_incident plus drawdown_threshold plus negative_pnl plus runtime_status_attention;"
        "Each rule emits a finding when matching reason present with severity bucket info-warn-alert per Pack D D-SentinelRules;"
        "After re-running Sentinel pass on the 13 degraded-personas fixture at least 13 findings (one per persona) appear in GET /bff/sentinel/findings?status=open;"
        "pytest covers each rule firing on its trigger reason plus not firing when reason absent;"
        "Documented gap (FE spec section 4): 13 personas currently degraded(85) with reasons [persona_lifecycle_not_active no_runtime_binding] produce 0 findings - this task closes that gap;"
        "Live verification after rule deploy: re-run probe and count findings against the 13 known-degraded fixture",
        f"{SPEC};{FE_SPEC};services/sentinel/",
    ),

    # ---------- EPIC-WRITE-GAP-OPS ----------
    (
        "OPS-BFF-LUPIN-DEV-REDEPLOY-20260528",
        "Re-deploy lupin dev BFF and verify all 15 write-gap routes live (babysit per feedback rule)",
        "Codex", "Claude", PHASE_OPS,
        "BFF-WRITE-P0-LIFECYCLE-001,BFF-WRITE-P0-LIFECYCLE-002,BFF-WRITE-P0-LIFECYCLE-003,BFF-WRITE-P0-LIFECYCLE-004,BFF-WRITE-P0-WIZARD-005,BFF-WRITE-P0-WIZARD-006,BFF-WRITE-P0-WIZARD-007,BFF-WRITE-P0-WIZARD-008,BFF-WRITE-P1-AGORA-009,BFF-WRITE-P1-AGORA-010,BFF-WRITE-P1-AGORA-011,BFF-WRITE-P1-AGORA-012,BFF-WRITE-P1-AGORA-013,BFF-WRITE-P1-AGORA-014,BFF-WRITE-P2-MISC-015",
        "Rebuild lupin dev BFF image from pantheon@origin/dev HEAD after all 15 BE tickets merged;"
        "Push image and roll out service; new pod ready;"
        "Run probe scripts from execute-plans: node scripts/probe-bff-write-paths.mjs and node scripts/probe-persona-onboarding-endpoints.mjs;"
        "All 15 write routes return Pack D-shaped 2xx success OR typed 4xx with canonical 26-code (NOT RESOURCE_NOT_FOUND with literal Not Found and NOT VALIDATION_FAILED with literal Method Not Allowed);"
        "Specifically: 7 actions return 202 (AdvanceLifecycle ApprovePool StartRuntime command-confirm approval-decisions inbox-triage batch-decide); 7 creates return 201 (bindings deployment-plans runtimes agora-signals agora-feedback agora-skill-coaching agora-postmortems); 1 read returns 200 with data.health field (persona-management/{id});"
        "Evidence committed to support/evidence/bff-write-gap-20260528/redeploy-curl-results.md with full curl-vN output plus probe.md regeneration;"
        "Do NOT mark this task done if any of the 15 routes returns 404 or 405 or untyped 5xx;"
        "Babysit rule from feedback_babysit_deploy_tasks: verify each route live before status transition to done",
        f"{SPEC};{FE_SPEC};{BE_BFF};{EVIDENCE_DIR}/redeploy-curl-results.md",
    ),
]


def update_sprint_metadata() -> None:
    """Update ai-status.json sprint id + objective before dispatching."""
    state = json.loads(STATE_PATH.read_text())
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-05-28T00:00:00Z"
    state["objective"] = SPRINT_OBJECTIVE
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    print(f"Sprint metadata updated: {SPRINT_ID}")


def dispatch_one(task_id: str, title: str, owner: str, reviewer: str,
                 phase: str, depends_on: str, acceptance: str, artifacts: str) -> None:
    env = os.environ.copy()
    env["AI_NAME"] = "Operator"
    env["TASK_PHASE"] = phase
    env["TASK_DEPENDS_ON"] = depends_on
    env["TASK_ACCEPTANCE"] = acceptance
    env["TASK_ARTIFACTS"] = artifacts
    cmd = [
        sys.executable,
        "scripts/ai_status.py",
        "assign",
        task_id,
        owner,
        reviewer,
        title,
    ]
    result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT),
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAIL {task_id}: {result.stderr.strip() or result.stdout.strip()}",
              file=sys.stderr)
        sys.exit(result.returncode)
    print(f"OK   {task_id}  owner={owner}  reviewer={reviewer}  phase={phase}")


def main() -> int:
    expected_ids = [t[0] for t in TASKS]
    if len(expected_ids) != len(set(expected_ids)):
        print("Duplicate task IDs in dispatch list", file=sys.stderr)
        return 1
    update_sprint_metadata()
    print(f"Dispatching {len(TASKS)} tasks ...")
    for task in TASKS:
        dispatch_one(*task)
    print(
        f"\nDispatched {len(TASKS)} tasks. "
        "Run `python3 scripts/ai_status.py sync` to refresh derived files. "
        "BABYSIT: do not mark OPS-BFF-LUPIN-DEV-REDEPLOY-20260528 done until live curl verified for all 15 routes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
