# BFF Role Vocabulary Mapping

**Created:** 2026-05-13
**Task:** BFF-CONSOL-006
**Author:** Claude
**Reviewer:** Codex
**Consumed by:** BFF-CONSOL-013 (cookie write gate), capability map, downstream RBAC tasks

---

## 1. Purpose

The Pantheon backend and its Lovable execute-plans frontend evolved separate role vocabularies.
This document makes the mismatch explicit, defines the canonical mapping, and specifies what
`GET /bff/me` must return in its `roles` field so both sides converge on the same set of role
strings without silent seed-data drift.

---

## 2. Backend Canonical Roles

Source: `services/control-plane/bff/main.py` — `_ROLE_CAPABILITY_MAP`, `_READ_ROLES`,
`_WRITE_ROLES`, `_require_admin_mfa`, and the stub token inference rules.

| Backend Role | Description | Read `/bff/me`? | Write Commands? | Notes |
|---|---|---|---|---|
| `admin` | Full platform control; MFA required for destructive commands | Yes | Yes | Holds all `EVIDENCE_CAPABILITY_MAP` capabilities |
| `approver` | Deployment & decision approval gate | Yes | Yes (approval-scoped) | Required for `ApproveDeployment`, `HardRollback`, `ApproveDecision` etc. |
| `operator` | Day-to-day operational commands | Yes | Yes | Required for `PauseExecution`, `IssueRiskOff`, `PauseRuntime`, MCP tool writes |
| `reviewer` | Strategy/persona review; evolution governance | Yes | Yes (review-scoped) | Required for `ApproveEvolutionDecision`, `ApproveMutation` |
| `analyst` | Metrics, jobs, audit read-only | **No** (403 on `/bff/me`) | No | Present in stub inference but excluded from `_READ_ROLES`; treat as legacy/internal only |
| `viewer` | Placeholder; no capabilities granted | **No** (403 on `/bff/me`) | No | Explicitly excluded from `_READ_ROLES` and `_WRITE_ROLES` |

`_READ_ROLES = {"operator", "approver", "admin", "reviewer"}`
`_WRITE_ROLES = {"operator", "approver", "admin", "reviewer"}`

**Important:** `viewer` and `analyst` cannot call `GET /bff/me`. Any frontend session with only
those roles will receive `403 INSUFFICIENT_ROLE`. Do not issue session tokens that carry only
`viewer` or `analyst` unless a lower-privilege UI path is explicitly wired for anonymous/limited access.

---

## 3. Frontend Mock Role Vocabulary

Source: `execute-plans/src/lib/v4/session/me.ts` — `Role` union type and `mockMe()`.

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

---

## 4. Role Mapping Table

| Frontend Mock Role | Backend Canonical Equivalent | Status | Migration Action |
|---|---|---|---|
| `platform_admin` | `admin` | **Deprecated (frontend-only)** | Replace with `admin` in backend claims; update Role type |
| `portfolio_manager` | `approver` | **Deprecated (frontend-only)** | Replace with `approver`; closest match covers deployment approval gate |
| `research_lead` | `reviewer` | **Deprecated (frontend-only)** | Replace with `reviewer`; covers strategy/evolution review |
| `ops` | `operator` | **Deprecated (frontend-only)** | Replace with `operator`; covers day-to-day operational commands |
| `viewer` | `viewer` | **Warning: both sides have `viewer` but it is blocked** | Keep the string; ensure UI treats viewer session as unauthenticated-equivalent for write gates |

All four non-`viewer` frontend mock roles are deprecated. They must not appear in production
session tokens or BFF responses. New callers must use backend canonical strings only.

---

## 5. `MeResponse.roles` Expected Output

The `GET /bff/me` response exposes roles at two paths:

```
data.roles          → same as data.user.roles (canonical backend strings)
data.user.roles     → List[str] — values from OperatorIdentity.roles
```

The frontend `MeResponse` interface's `roles: Role[]` field should be widened to
`roles: (Role | string)[]` during migration so it can accept backend canonical strings without
TypeScript errors, until the `Role` union is updated to match the backend vocabulary exactly.

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
Explicit capability claims from the auth service always override this fallback map.

---

## 7. Command Risk Thresholds by Role

Source: `_RISK_GATE_ROLES` and validation functions in `main.py`.

| Risk Level | Read (`runAction` category) | Write (`/bff/v1/commands`) |
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

The `auth_mode` field already exists in the session payload (`identity.token_kind`). BFF-CONSOL-013
will surface it as `session_kind` in the `/bff/me` data.session object and update `liveWriteGated()`
to read `session_kind` instead of checking only `sessionStorage` bearer token.

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

- [ ] Update `Role` union type in `me.ts` to include backend canonical strings
- [ ] Update `mockMe()` to return `roles: ["operator"]` (or `["approver"]` for approval-scoped tests)
- [ ] Update `liveWriteGated()` to read `session.auth_mode` (and eventually `session.session_kind` from BFF-CONSOL-013)
- [ ] Ensure no seed data or fixture passes `portfolio_manager`, `ops`, `platform_admin`, or `research_lead` in `roles`
- [ ] Verify that `/bff/me` tests use backend canonical role strings, not frontend mock strings
- [ ] Confirm cookie-session tests send a role of at least `operator` or `reviewer` so `/bff/me` returns 200
