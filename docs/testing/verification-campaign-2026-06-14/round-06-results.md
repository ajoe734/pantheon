# Round 6 — Results

**Executed:** 2026-06-14 (UTC). **Method:** static authorization audit of all
136 `@app.{post,put,patch,delete}` handlers in
`services/control-plane/bff/main.py`, plus targeted spot-reads.

## Authorization coverage

Under gate-aware detection (role functions incl. domain-specific
`_require_source_search_command_role`, `_require_agora_signal_write_role`,
`_require_journal_write_role`, `_MCP_TOOL_WRITE_ROLES`; inline role-set
intersections like `{"approver","admin"}.intersection(identity.roles)`;
`_WRITE_ROLES`/role-set constants; and deprecated-route short-circuits):

| Class | Count |
|---|---|
| Write-gated (role fn / inline roles / role-set / deprecated) | 133 |
| Auth endpoints (login/refresh — intentionally pre-auth) | 2 |
| Read-gate-only **persisting** writes | 3 (see F7) |

**H2 PASS (spot-verified).** Confirmed enforcement on high-risk handlers:
- `bff_approvals_decide`, `create_approval_decision` — inline `{approver,admin}`
  → 403.
- `replay_source_dlq`, `trigger_search_index_refresh` —
  `_require_source_search_command_role`.
- `bff_create_agora_signal` — `_require_agora_signal_write_role`.
- `bff_capital_pool_action` — read-gate then command/precondition machinery
  (and registered-action allowlist; non-registered actions are deprecated).

## Methodology note

A naive static audit (matching only `_require_operator_role`) over-flagged 85/136
handlers as "ungated". The BFF actually uses a **rich gate vocabulary** —
many domain-specific write-role functions and inline role-set checks — so
authorization is far better than a first pass suggests. Recommend turning this
audit into a **maintained authz inventory test** so coverage drift is visible.

## Findings

### F7 — three generic create endpoints gated by read role only (LOW; product decision, not changed)

`create_research_note` (`POST /api/v1/knowledge/notes`), `bff_create_tool`
(`POST /bff/tools`), and `bff_create_evolution_program`
(`POST /bff/evolution-programs`) call only `_require_read_role`, which admits the
read-only `viewer` role, then persist via `read_store.create_*`. A `viewer`
principal could therefore create these records.

Severity is **low**: these are non-capital authoring/descriptor records, not
trade/capital/deployment actions (all of which are write-gated). Intent is
**ambiguous** — broad authoring may be deliberate (cf. `create_research_note`
even rejects caller-supplied `owner_ref`, showing security awareness), while the
analogous MCP tool-import route *is* operator-gated (test
`test_tool_action_rejects_missing_tool_viewer_role...`).

**Decision: not changed in this round.** Tightening these to
`_require_operator_role` is a security-sensitive, client-visible authz change;
applying it unilaterally risks breaking dev/FE flows that may rely on the
current behavior. Recommendation for product/security triage: confirm the
intended author-role for each resource and, if write-level is intended, switch
the three gates to `_require_operator_role` with a `viewer`→403 regression test
(mirroring the MCP-import test). Queued, not force-applied.

## Net

H1 substantially holds — 133/136 mutating routes are write-gated and every
high-risk handler provably enforces a write role. One low-severity,
intent-ambiguous authz-consistency item (F7) is documented for product decision
rather than changed. No code change this round; the value is the verified authz
map + the maintained-inventory recommendation.
