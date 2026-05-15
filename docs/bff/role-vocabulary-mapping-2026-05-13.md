# BFF Role Vocabulary Mapping

**Created:** 2026-05-13
**Task:** BFF-CONSOL-006
**Owner:** Codex
**Reviewer:** Claude
**Consumed by:** BFF-CONSOL-013 (cookie write gate), capability map, downstream RBAC tasks

---

## 1. Purpose

The Pantheon backend and its Lovable execute-plans frontend evolved separate role vocabularies.
This document makes the mismatch explicit, defines the canonical mapping, and specifies what
`GET /bff/me` must return in its `roles` field so both sides converge on the same set of role
strings without silent seed-data drift.

This is a task-scoped BFF consolidation artifact. It documents current implementation truth and
consumer migration rules; it does not replace the L1 authorization policies.

---

## 2. Backend Role Vocabulary

Source: `services/control-plane/bff/main.py` — `_ROLE_CAPABILITY_MAP`, `_READ_ROLES`,
`_WRITE_ROLES`, `_require_admin_mfa`, `/bff/me`, and the stub/JWT token inference rules.

Current `/bff/me` read admission is `_READ_ROLES = {"operator", "approver", "admin", "reviewer"}`.
`viewer` is a named backend role but is not accepted by `/bff/me`.

| Backend Role | Description | Read `/bff/me`? | Write Commands? | Notes |
|---|---|---|---|---|
| `admin` | Full platform control; MFA required for destructive commands | Yes | Yes | Holds all `EVIDENCE_CAPABILITY_MAP` capabilities |
| `approver` | Deployment & decision approval gate | Yes | Yes (approval-scoped) | Required for `ApproveDeployment`, `HardRollback`, `ApproveDecision` etc. |
| `operator` | Day-to-day operational commands | Yes | Yes | Required for `PauseExecution`, `IssueRiskOff`, `PauseRuntime`, MCP tool writes |
| `reviewer` | Strategy/persona review; evolution governance | Yes | Yes (review-scoped) | Required for `ApproveEvolutionDecision`, `ApproveMutation` |
| `analyst` | Metrics, jobs, audit read-only | **No** (403 on `/bff/me`) | No | Present in stub inference but excluded from `_READ_ROLES`; treat as legacy/internal only |
| `viewer` | Placeholder/low-privilege default | **No** (403 on `/bff/me`) | No | Explicitly excluded from `_READ_ROLES` and `_WRITE_ROLES`; do not use for authenticated execute-plans sessions |

`_READ_ROLES = {"operator", "approver", "admin", "reviewer"}`
`_WRITE_ROLES = {"operator", "approver", "admin", "reviewer"}`

**Important:** `viewer` and `analyst` cannot call `GET /bff/me`. Any frontend session with only
those roles will receive `403 INSUFFICIENT_ROLE`. Do not issue session tokens that carry only
`viewer` or `analyst` unless a lower-privilege UI path is explicitly wired for anonymous/limited access.

---

## 3. Frontend Mock Role Vocabulary

Sources in the execute-plans frontend repo:

- `src/lib/v4/session/me.ts` — `Role` union type, `MeResponse.roles`, and `mockMe()`
- `src/lib/v4/roleCapabilities.ts` — wider legacy/frontend-only role and capability bundle

```typescript
export type Role =
  | "platform_admin"
  | "portfolio_manager"
  | "research_lead"
  | "ops"
  | "viewer";
```

Mock function returns `roles: ["portfolio_manager", "ops"]`.

These role strings were defined in the Lovable frontend before BFF integration. They are
**frontend-only labels** that do not exist in the backend role system. The `/bff/me` endpoint
returns backend canonical role strings (e.g., `"operator"`, `"approver"`); the frontend `Role`
type must be updated to match or widened to accept both sets during migration.

Important frontend mismatch: `roleCapabilities.ts` already includes `admin` and `reviewer`, but it
does not include backend `operator` or `approver`. It also grants frontend `viewer` read capabilities,
while backend `viewer` receives `403` from `/bff/me`. Consumers must prefer `/bff/me.capabilities`
over frontend role-derived capability inference whenever live BFF data is present.

---

## 4. Role Mapping Table

| Frontend Role | Current Frontend Meaning | Backend `/bff/me` Target | Status | Consumer Rule |
|---|---|---|---|---|
| `platform_admin` | Frontend superuser/admin bucket | `admin` | **Deprecated frontend-only alias** | Replace in live tokens and BFF responses with `admin` |
| `portfolio_manager` | Capital/rebalance/approval-facing mock role | `approver` | **Deprecated frontend-only alias** | Use `approver` for approval-gate/session tests; preserve capital permissions through `capabilities`, not role inference |
| `research_lead` | Strategy/research/evolution review bucket | `reviewer` | **Deprecated frontend-only alias** | Use `reviewer` for strategy/persona/evolution review access |
| `ops` | Runtime/operator action bucket | `operator` | **Deprecated frontend-only alias** | Use `operator` for day-to-day live read/write gate checks |
| `viewer` | Frontend read-only UI role | No admitted `/bff/me` role today | **Name collision** | Do not issue viewer-only execute-plans sessions; backend returns `403 INSUFFICIENT_ROLE` |
| `admin` | Already present in frontend capability bundle | `admin` | Canonical backend role | Keep, but use backend capability claims for live behavior |
| `reviewer` | Already present in frontend capability bundle | `reviewer` | Canonical backend role | Keep, but ensure session `Role` union also accepts it |
| `risk_officer`, `capital_manager`, `strategy_manager`, `system_operator`, `capability_admin` | Legacy/frontend capability families | None unless IdP role map converts them | Frontend-only for BFF `/bff/me` | Do not send as final BFF role strings without `PANTHEON_BFF_ROLE_MAP` coverage |

All frontend-only aliases are deprecated for live BFF sessions. They must not appear in production
session tokens or `/bff/me` responses. New callers must use backend canonical strings only:
`operator`, `approver`, `admin`, and `reviewer`.

---

## 5. `MeResponse.roles` Expected Output

The current backend `GET /bff/me` response exposes roles at two paths:

```
data.roles          → same as data.user.roles (canonical backend strings)
data.user.roles     → List[str] — values from OperatorIdentity.roles
```

Live `/bff/me` consumers must preserve those backend role strings. They must not translate them
back to `portfolio_manager` / `ops`.

Recommended frontend typing during migration:

```typescript
export type BackendKnownRole =
  | "operator"
  | "approver"
  | "admin"
  | "reviewer"
  | "viewer";

export type BackendMeRole = Exclude<BackendKnownRole, "viewer">;

export type DeprecatedFrontendRole =
  | "platform_admin"
  | "portfolio_manager"
  | "research_lead"
  | "ops";

export type Role = BackendKnownRole | DeprecatedFrontendRole;

export interface MeResponse {
  roles: BackendMeRole[]; // successful live /bff/me output
  capabilities: string[]; // preferred source for feature gates
}
```

If the frontend temporarily uses `roles: string[]`, unknown role strings must not imply
capabilities. Capability checks should read `/bff/me.capabilities`.

### Example backend response for a typical operator session

```json
{
  "data": {
    "user": {
      "id": "op-7",
      "operator_id": "op-7",
      "roles": ["operator"],
      "capabilities": ["runtime.read", "risk.incident.read", "risk.alert.read", "artifact.read"]
    },
    "roles": ["operator"],
    "session": {
      "authenticated": true,
      "auth_mode": "jwt"
    }
  }
}
```

### Example backend response for an approver session

```json
{
  "data": {
    "user": {
      "id": "op-12",
      "roles": ["approver"],
      "capabilities": ["approval.read", "postmortem.read", "policy.read"]
    },
    "roles": ["approver"]
  }
}
```

---

## 6. Role-to-Capability Map

Source: `_ROLE_CAPABILITY_MAP` in `main.py` (fallback when auth service does not supply claims).

| Backend Role | Derived Capabilities |
|---|---|
| `admin` | All of `EVIDENCE_CAPABILITY_MAP` values (risk.alert.read, risk.incident.read, job.read, audit.read, metric.read, strategy.view, persona.view, deployment.read, runtime.read, policy.read, approval.read, artifact.read, agora.signal.read, agora.journal.read, postmortem.read) |
| `approver` | `approval.read`, `postmortem.read`, `policy.read` |
| `operator` | `runtime.read`, `risk.incident.read`, `risk.alert.read`, `artifact.read` |
| `reviewer` | `approval.read`, `strategy.view`, `persona.view` |
| `analyst` | `metric.read`, `job.read`, `audit.read` |
| `viewer` | _(none)_ |

Capabilities are additive. A user with `["operator", "reviewer"]` receives the union of both sets.
Explicit capability claims from the auth service are merged with role-derived capabilities and
deduped (`capabilities = dedupe([*claim_caps, *_capabilities_for_identity(identity)])`); auth-service
claims extend the fallback map, not replace it.

---

## 7. Mutation Risk Gate by Role

Source: `_MUTATION_APPROVAL_ROLES`, `_MUTATION_REJECTION_ROLES`, and `_require_admin_mfa`
in `main.py`. Used by `_mutation_review_roles_for(risk_level, action=)`.

| Risk Level | Can approve mutation | Can reject mutation |
|---|---|---|
| `low` | reviewer, approver, admin | reviewer, approver, admin |
| `medium` | operator, approver, admin | reviewer, operator, approver, admin |
| `high` | approver, admin | approver, admin |
| Admin+MFA | admin only | admin only (`LiquidateAll`, `IssueSafeMode`) |

---

## 8. Session Kind (Planned for BFF-CONSOL-013)

BFF-CONSOL-013 will add a `session_kind` field to the `/bff/me` response to allow the frontend
`liveWriteGated()` function to distinguish how the session was authenticated:

| `session_kind` | Meaning | Write Gate |
|---|---|---|
| `bearer` | JWT Bearer token in `Authorization` header | Pass if role ≥ operator |
| `cookie` | Cookie-based session (Lovable preview / prod) | Pass if role ≥ operator |
| `stub` | Auth stub mode (`PANTHEON_BFF_AUTH_STUB=true`) | Block in production strict mode |

Current backend response already includes `data.session.auth_mode = identity.token_kind`, and
frontend live requests already use `credentials: "include"` plus optional `Authorization: Bearer`.
That is not sufficient for cookie write gating because it does not explicitly distinguish a
cookie-authenticated browser session from a Bearer-token session. BFF-CONSOL-013 must add
`data.session.session_kind` (or an equivalent stable field) and update `liveWriteGated()` to use
that backend-confirmed value instead of checking only `sessionStorage` bearer-token presence.

---

## 9. Deprecated Frontend-Only Roles

The following frontend `Role` union members are deprecated and must not be used in new code:

| Deprecated Role | Reason | Replacement |
|---|---|---|
| `platform_admin` | Frontend-only label; no backend equivalent | `admin` |
| `portfolio_manager` | Frontend-only label; no backend equivalent | `approver` |
| `research_lead` | Frontend-only label; no backend equivalent | `reviewer` |
| `ops` | Frontend-only label; no backend equivalent | `operator` |

`viewer` is shared between frontend and backend but carries special meaning: it denies BFF read
access. Frontend code must handle the case where a `viewer` session receives 403 from `/bff/me`.

---

## 10. Migration Checklist for Consumers

- [ ] Update `Role` union type in `me.ts` to include backend canonical strings: `operator`, `approver`, `admin`, `reviewer`, `viewer`
- [ ] Update `MeResponse.roles` live type to backend role strings; keep deprecated frontend aliases only for mock/legacy compatibility
- [ ] Update `mockMe()` to return `roles: ["operator", "approver"]` if preserving the current `["portfolio_manager", "ops"]` semantics, or a narrower role set for focused tests
- [ ] Update `liveWriteGated()` to read backend-confirmed `session.session_kind` once BFF-CONSOL-013 lands; do not infer cookie auth from `sessionStorage` alone
- [ ] Ensure no seed data or fixture passes `portfolio_manager`, `ops`, `platform_admin`, or `research_lead` in `roles`
- [ ] Verify that `/bff/me` tests use backend canonical role strings, not frontend mock strings
- [ ] Confirm cookie-session tests send a role of at least `operator` or `reviewer` so `/bff/me` returns 200

---

## 11. Verification Notes

Sources inspected for this mapping:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_session_auth_me_contract.py`
- `../execute-plans/src/lib/v4/session/me.ts`
- `../execute-plans/src/lib/v4/roleCapabilities.ts`
- `../execute-plans/src/lib/bff-v1/client.ts`

Focused verification for this doc should include:

```bash
python3 -m pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
git diff --check -- docs/bff/role-vocabulary-mapping-2026-05-13.md
```
