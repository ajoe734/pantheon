#!/usr/bin/env python3
"""One-shot dispatcher for the 2026-06-03 Management AI conversation persistence tasks.

BE-view spec:   docs/04/pantheon_management_ai_persistence_2026-06-03/MANAGEMENT_AI_PERSISTENCE_GAP_SPEC.md
Audit:          docs/04/pantheon_management_ai_persistence_2026-06-03/CURRENT_STATE_AUDIT.md
FE requirement: docs/04/pantheon_management_ai_persistence_2026-06-03/UPSTREAM_FE_REQUIREMENT_2026-06-03.md

Today /bff/management/nl/ask + /bff/management/ai/conversations/{sessionId} only
emit an ephemeral in-memory deque + /tmp JSONL audit log. No durable store, no
server-side history fed to the provider, no attachments, and GET returns
200+{turns:[]} for unknown sessions. This dispatcher emits 8 tasks across 5 EPICs:

  EPIC-MGMT-AI-PERSIST-P0-STORE   (1) - Postgres AssistantConversationStore (shared substrate)
  EPIC-MGMT-AI-PERSIST-P0-WRITE   (3) - nl/ask persist + history-to-provider + idempotency
  EPIC-MGMT-AI-PERSIST-P0-READ    (1) - GET conversations store-backed + 404 + scope
  EPIC-MGMT-AI-PERSIST-P1-ATTACH  (2) - attachment storage + multimodal forward
  EPIC-MGMT-AI-PERSIST-OPS        (1) - provision Postgres + redeploy + live-verify 7 criteria

Owner split (2026-05-24 delta 3-class pattern):
  P0 core + OPS  -> Codex  / Claude
  P1 attachments -> Codex2 / Claude2

STORAGE DECISION (operator 2026-06-03): Option B Postgres - built on the existing
platform foundation (services/foundation/postgres_json_store.py + persistence_posture)
NOT a new psycopg layer. This is the shared assistant-conversation substrate the
future Agora helper reuses (fewer features) with SEPARATE ACCOUNTS per surface (own
owner role + own schema) - which is the foundation's documented service-owned-table +
read-only-role convention. Backend selector defaults to json in dev and enforces
postgres in staging/prod. WRITE/READ are store-agnostic against the
AssistantConversationStore interface so Agora reuses them by pointing at its own
role/schema. See spec section 2.

Babysit rule (feedback_babysit_deploy_tasks): do not mark the OPS task done until
all 7 acceptance criteria are verified by live curl on the dev BFF.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "ai-status.json"

ARCHIVE = "docs/04/pantheon_management_ai_persistence_2026-06-03"
SPEC = f"{ARCHIVE}/MANAGEMENT_AI_PERSISTENCE_GAP_SPEC.md"
AUDIT = f"{ARCHIVE}/CURRENT_STATE_AUDIT.md"
FE_REQ = f"{ARCHIVE}/UPSTREAM_FE_REQUIREMENT_2026-06-03.md"
BE_BFF = "services/control-plane/bff/main.py"
BE_STORE = "services/control-plane/bff/assistant_conversation_store.py"
BE_FOUNDATION = "services/foundation/postgres_json_store.py;services/foundation/persistence_posture.py"
BE_REQS = "services/control-plane/bff/requirements.txt"
TEST_FILE = "services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py"
EVIDENCE_DIR = "support/evidence/mgmt-ai-persistence-20260603"

PHASE_STORE = "Sprint MGMT-AI-PERSIST / EPIC-MGMT-AI-PERSIST-P0-STORE"
PHASE_WRITE = "Sprint MGMT-AI-PERSIST / EPIC-MGMT-AI-PERSIST-P0-WRITE"
PHASE_READ = "Sprint MGMT-AI-PERSIST / EPIC-MGMT-AI-PERSIST-P0-READ"
PHASE_ATTACH = "Sprint MGMT-AI-PERSIST / EPIC-MGMT-AI-PERSIST-P1-ATTACH"
PHASE_OPS = "Sprint MGMT-AI-PERSIST / EPIC-MGMT-AI-PERSIST-OPS"

SPRINT_ID = "2026-06-03-pantheon-mgmt-ai-persistence"
SPRINT_OBJECTIVE = (
    "Pantheon Management AI conversation persistence (P0) - today "
    "POST /bff/management/nl/ask and GET /bff/management/ai/conversations/{sessionId} "
    "only emit an ephemeral in-memory deque plus /tmp JSONL audit log so multi-turn "
    "correctness is broken: provider gets no conversation history (neither DB nor FE "
    "recentTurns) the GET returns 200+empty-turns for unknown sessions there is no "
    "attachment storage and stored text is truncated to 400 chars. Five EPICs close "
    "the gap: P0-STORE (1 task - durable session+turn+idempotency store replacing the "
    "deque+/tmp via a Postgres-backed AssistantConversationStore interface per spec "
    "section 2); P0-WRITE (3 task - nl/ask persists full "
    "user+assistant turns plus build provider context from server-side history with "
    "recentTurns demoted to a hint plus durable Idempotency-Key replay with zero "
    "duplicate turns); P0-READ (1 task - GET conversations store-backed created_at "
    "ascending 404 on unknown session and owner/tenant scope); P1-ATTACH (2 task - "
    "base64 attachments to object storage with metadata-only rows plus signed-URL "
    "reads plus multimodal forward to OpenClaw/Codex); OPS (1 task - provision Postgres "
    "plus redeploy dev BFF and live-verify all 7 acceptance criteria per babysit rule). "
    "STORAGE: Option B real Postgres - shared assistant-conversation substrate the "
    "future Agora helper reuses with SEPARATE ACCOUNTS (own role plus own schema per "
    "surface: management_ai schema/role now agora_ai later). Owner split matches "
    "the 2026-05-24 delta 3-class pattern: P0 core plus OPS go Codex/Claude; P1 "
    "attachments go Codex2/Claude2. Spec: "
    "docs/04/pantheon_management_ai_persistence_2026-06-03/MANAGEMENT_AI_PERSISTENCE_GAP_SPEC.md. "
    "This is the P0 extension of the 2026-05-28 BFF write-gap SoT."
)

# (task_id, title, owner, reviewer, phase, depends_on, acceptance, artifacts)
TASKS = [
    # ---------- EPIC-MGMT-AI-PERSIST-P0-STORE ----------
    (
        "MGMT-AI-PERSIST-P0-STORE-001",
        "Postgres AssistantConversationStore (shared substrate) replacing deque + /tmp jsonl",
        "Codex", "Claude", PHASE_STORE,
        "",
        # NOTE: acceptance is split on ',' by parse_csv_env - avoid commas; use 'plus'.
        "REUSE the platform foundation - do NOT roll a new psycopg layer: build on services/foundation/postgres_json_store.py (quote_pg_identifier plus lazy import psycopg plus CREATE SCHEMA/TABLE IF NOT EXISTS plus service-owned-table plus read-only-role rule) and register the BFF in services/foundation/persistence_posture.py;"
        "Create services/control-plane/bff/assistant_conversation_store.py exposing an AssistantConversationStore interface with create_session get_session list_turns append_turn touch_session and idempotency get/put; reuse PostgresJsonOwnerStore directly where a KV record suffices (sessions) and add a small relational turns table where ordered listing plus session FK are needed;"
        "Add a backend selector MANAGEMENT_AI_STORE_BACKEND matching the other services: default json/in-memory for local-dev and enforce postgres in staging/prod via persistence_posture ENFORCED_MODES; ensure psycopg is available for the postgres backend;"
        "Bootstrap creates sessions plus turns plus idempotency from spec section 3 inside the management_ai schema with JSONB columns TIMESTAMPTZ defaults index on (session_id created_at) and FK ON DELETE CASCADE; the store is surface-parameterized (schema plus owner role) so the Agora helper reuses the same class against agora_ai - do NOT hardcode management_ai names in handler-facing methods;"
        "Turn text column stores FULL text with no 400-char truncation; attachments provider_status ui_snapshot are JSONB;"
        "Store is multi-writer safe (transactional append) so multiple BFF workers share one source of truth;"
        "Tests cover both backends: json backend runs unconditionally; postgres backend runs against env TEST_DATABASE_URL and skips with a clear message when unset;"
        "pytest in test_bff_mgmt_ai_persistence_2026_06_03.py covers create/get session round-trip plus append+list 60 turns in created_at order plus idempotency put/get plus unknown-session returns None plus durability across a fresh connection (simulated restart);"
        "Wire main.py to instantiate the store once at startup behind a module-level accessor so handlers use it",
        f"{SPEC};{AUDIT};{BE_BFF};{BE_STORE};{BE_FOUNDATION};{BE_REQS};{TEST_FILE}",
    ),

    # ---------- EPIC-MGMT-AI-PERSIST-P0-WRITE ----------
    (
        "MGMT-AI-PERSIST-P0-WRITE-002",
        "POST /bff/management/nl/ask - persist user + assistant turns durably (handler main.py:30467)",
        "Codex", "Claude", PHASE_WRITE,
        "MGMT-AI-PERSIST-P0-STORE-001",
        "Rewrite bff_management_nl_ask at services/control-plane/bff/main.py:30467 to use the AssistantConversationStore;"
        "If body has no sessionId/session_id create a session row with owner_id=identity.operator_id plus tenant_id=caller tenant and return canonical sessionId; if id present reuse-or-create with that id;"
        "Persist the user turn before calling the provider with role=user FULL text attachments ui_snapshot trace_id and stable turn id;"
        "After the provider call persist the assistant turn with role=assistant FULL answer provider_status trace_id and ui_actions;"
        "Remove the 400-char _management_ai_summary_value truncation from the persisted text path (audit-event sidecar may keep its summary);"
        "Keep the existing 202 response envelope and all current keys plus ensure sessionId traceId providerStatus answer uiActions auditLog are present;"
        "High-risk refusal classify still runs before any session create or write;"
        "Audit events via _management_ai_record_event remain as a debug sidecar only - store is the source of truth;"
        "pytest case persist_turns covers new-session creation plus reuse plus that 30 asks yield 60 stored turns with full text",
        f"{SPEC};{BE_BFF};{BE_STORE};{TEST_FILE}",
    ),
    (
        "MGMT-AI-PERSIST-P0-WRITE-003",
        "Build provider context from server-side history (recentTurns demoted to hint)",
        "Codex", "Claude", PHASE_WRITE,
        "MGMT-AI-PERSIST-P0-WRITE-002",
        "Augment _mgmt_nl_provider_prompt path (main.py:30229) so the provider receives the full stored conversation history for the session ordered created_at ascending - not only question plus focus plus context_pack;"
        "Server-side history from the store is authoritative; FE conversation.recentTurns is treated as a hint only and never replaces stored truth;"
        "Apply a sane provider-side token/char budget when assembling history (oldest-first summarize or window) but the budget must exceed the FE 32KB window so server history is strictly richer;"
        "Acceptance: with FE sending only the last 2 recentTurns the assembled provider prompt still contains earlier turns from the store;"
        "pytest case history_to_provider asserts the provider prompt includes a turn that exists in the store but was omitted from the FE recentTurns payload",
        f"{SPEC};{BE_BFF};{BE_STORE};{TEST_FILE}",
    ),
    (
        "MGMT-AI-PERSIST-P0-WRITE-004",
        "Durable Idempotency-Key - replay original response with zero duplicate turns",
        "Codex", "Claude", PHASE_WRITE,
        "MGMT-AI-PERSIST-P0-WRITE-002",
        "Replace the in-memory _MGMT_NL_IDEMPOTENCY dict (main.py:29251) with the store idempotency table;"
        "On nl/ask resolve the key via _resolve_final_idempotency_key then check the store: matching request_hash replays the stored response_json and writes ZERO new turns; missing key proceeds and records key+hash+response after success;"
        "Mismatched request_hash for the same key keeps the existing 409 behavior;"
        "Idempotency survives a BFF restart (durable) and is consistent across workers when the store is shared;"
        "pytest case idempotency_no_dup covers replay returns identical body plus turn count unchanged after replay plus restart-then-replay still dedups",
        f"{SPEC};{BE_BFF};{BE_STORE};{TEST_FILE}",
    ),

    # ---------- EPIC-MGMT-AI-PERSIST-P0-READ ----------
    (
        "MGMT-AI-PERSIST-P0-READ-005",
        "GET /bff/management/ai/conversations/{sessionId} - store-backed + 404 + owner/tenant scope (handler main.py:30780)",
        "Codex", "Claude", PHASE_READ,
        "MGMT-AI-PERSIST-P0-STORE-001",
        "Rewrite bff_management_ai_conversation at services/control-plane/bff/main.py:30780 to read turns from the AssistantConversationStore ordered created_at ascending;"
        "Return 404 RESOURCE_NOT_FOUND when the session row does not exist - do NOT return 200 with empty turns;"
        "Owner/tenant scope: only return sessions owned by the caller or visible in the caller tenant; a foreign session returns 404 and does not leak existence;"
        "Each turn includes the FE camelCase shape id role text createdAt providerStatus and attachments[] with kind mimeType filename sizeBytes url; keep existing snake_case mirrors for back-compat;"
        "pytest case read_conversations covers ascending order plus 404 on unknown session plus 404 on foreign tenant plus full 60-turn read plus turn id stability",
        f"{SPEC};{BE_BFF};{BE_STORE};{TEST_FILE}",
    ),

    # ---------- EPIC-MGMT-AI-PERSIST-P1-ATTACH ----------
    (
        "MGMT-AI-PERSIST-P1-ATTACH-006",
        "Attachment ingest to object storage - DB stores metadata + storageUrl (not base64)",
        "Codex2", "Claude2", PHASE_ATTACH,
        "MGMT-AI-PERSIST-P0-WRITE-002",
        "On nl/ask decode FE attachments[].dataBase64 and upload bytes to object storage (GCS bucket from env PANTHEON_MGMT_AI_ATTACH_BUCKET reusing existing BFF VM GCS creds);"
        "Persist ONLY metadata in the turn attachments JSON: kind mimeType filename sizeBytes storageUrl - never store base64 in the store;"
        "Enforce a per-attachment size cap plus per-request total cap plus a mimeType allowlist (images first) returning 413 or 422 on violation;"
        "GET conversations returns attachments[].url as a signed URL (or a BFF proxy URL) openable in a browser - never base64;"
        "pytest case attachment_storage covers base64 in -> storageUrl stored plus no base64 in the store row plus GET returns a signed/proxy url plus oversize rejected;"
        "Live verification after OPS: upload a small PNG then GET the conversation and open the returned url",
        f"{SPEC};{BE_BFF};{BE_STORE};{TEST_FILE}",
    ),
    (
        "MGMT-AI-PERSIST-P1-ATTACH-007",
        "Forward attachments to OpenClaw/Codex provider as multimodal payload",
        "Codex2", "Claude2", PHASE_ATTACH,
        "MGMT-AI-PERSIST-P1-ATTACH-006",
        "When the provider is called and the latest user turn has image attachments include them in the provider request as a multimodal payload (image_url or base64 per provider requirement);"
        "Resolve attachment bytes/url from object storage not from the store row;"
        "Degrade gracefully: if the provider does not support multimodal record provider_status reason multimodal_unsupported and fall back to text-only without failing the request;"
        "pytest case multimodal_forward covers image present -> provider payload carries the image part plus unsupported-provider fallback",
        f"{SPEC};{BE_BFF};{TEST_FILE}",
    ),

    # ---------- EPIC-MGMT-AI-PERSIST-OPS ----------
    (
        "OPS-MGMT-AI-PERSIST-REDEPLOY-20260603",
        "Redeploy dev BFF (persistent volume + bucket) and live-verify all 7 acceptance criteria (babysit)",
        "Codex", "Claude", PHASE_OPS,
        "MGMT-AI-PERSIST-P0-STORE-001,MGMT-AI-PERSIST-P0-WRITE-002,MGMT-AI-PERSIST-P0-WRITE-003,MGMT-AI-PERSIST-P0-WRITE-004,MGMT-AI-PERSIST-P0-READ-005,MGMT-AI-PERSIST-P1-ATTACH-006,MGMT-AI-PERSIST-P1-ATTACH-007",
        "Provision the Postgres instance plus the management_ai schema plus the pantheon_management_ai role (granted ONLY the management_ai schema) plus the MANAGEMENT_AI_DATABASE_URL secret on the lupin dev BFF; leave a documented path to add agora_ai schema plus pantheon_agora_ai role later;"
        "Provision the GCS attachment bucket from PANTHEON_MGMT_AI_ATTACH_BUCKET;"
        "Set MANAGEMENT_AI_STORE_BACKEND=postgres for dev/staging/prod and let the store bootstrap (foundation CREATE IF NOT EXISTS) create sessions plus turns plus idempotency on first connect; confirm persistence_posture passes so the service refuses to start without a real DB in enforced modes;"
        "Rebuild and roll out the dev BFF image from pantheon@origin/dev HEAD after all 7 BE tickets merge; new pod ready;"
        "Live-verify against https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io: (1) 30 asks in one sessionId then GET returns 60 turns full text ascending; (2) restart pod then GET same sessionId still returns full history; (3) send only 2 recentTurns and confirm answer still reflects earlier context; (4) upload a PNG then GET returns an openable signed url not base64; (5) unknown sessionId returns 404 not 200+empty; (6) replay Idempotency-Key yields no duplicate turn; (7) foreign-tenant sessionId returns 404;"
        "Commit evidence to support/evidence/mgmt-ai-persistence-20260603/live-verify.md with full curl output per criterion;"
        "Do NOT mark this task done if any of the 7 criteria fail;"
        "Babysit rule from feedback_babysit_deploy_tasks: verify each criterion live before status transition to done",
        f"{SPEC};{BE_BFF};{EVIDENCE_DIR}/live-verify.md",
    ),
]


def update_sprint_metadata() -> None:
    """Update ai-status.json sprint id + objective before dispatching."""
    state = json.loads(STATE_PATH.read_text())
    state["sprint"] = SPRINT_ID
    state["sprint_started_at"] = "2026-06-03T00:00:00Z"
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
        "BABYSIT: do not mark OPS-MGMT-AI-PERSIST-REDEPLOY-20260603 done until all 7 "
        "acceptance criteria are live-verified."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
