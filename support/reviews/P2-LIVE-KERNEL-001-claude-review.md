# Review: P2-LIVE-KERNEL-001 — Full Lean Launcher + broker SDK production readiness plan

Reviewer: Claude
Task owner: Codex
Date: 2026-05-01
Status: **APPROVED**

---

## Artifacts reviewed

- `docs/04/CANARY_LIVE_ACTIVATION_CRITERIA_AND_RUNBOOK.md`
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`

---

## Acceptance criteria evaluation

### A1 — Lean Launcher plus broker SDK production readiness plan is documented without enabling live by default

**PASS.**

§1.2 explicitly scopes the addendum as a readiness plan only, not an activation. §9 (new) defines the full production readiness requirements across nine subsections without enabling any order-capable execution. §9.5 gap register explicitly marks every live-enabling item as `Missing; live activation rejected`. The document posture throughout is fail-closed: live remains `health_only/fail-closed` until all enumerated conditions are met simultaneously.

### A2 — Broker entitlement subaccount isolation and capital authorization gaps are resolved or explicitly fail-closed

**PASS.**

§6.4 defines stage-scoped entitlement requirements (paper / canary / live). The table in §6.4 has an explicit `Missing evidence behavior` column: promotion controller rejects activation and records the missing entitlement. §6.5 defines a self-contained capital authorization procedure including approved scale limits, effective window, and revocation rule. §9.5 gap register explicitly lists `Production live entitlement packet`, `Dedicated canary/live subaccount evidence`, and `Capital authorization for live exposure` all as `Missing; live activation rejected`. These are described as preconditions, not warnings.

### A3 — Paper/canary/live promotion gates reference kill-switch ack and drill prerequisites before any live activation

**PASS.**

`KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` §8.2 (new) defines activation gate rules: canary requires a paper-stage drill returning `telemetry_ack.ack_status = acknowledged`; live requires a canary/staging-live drill for the active `RuntimeBinding`, `capital_pool_id`, and target broker subaccount. `fail_closed` ack blocks promotion. The canary gate table in runbook §6.1 now includes an explicit `Kill-switch drill and ack gate` row. The live gate table in §6.2 adds a `Live kill-switch drill and ack gate` row. The stage promotion flow diagram in §8 references the acknowledged ack at both paper→canary and canary→live transitions. §1.3 hard invariants (items 11–12) reinforce these rules at the invariant level.

---

## Consistency checks

- `OPENCLAW_RUNTIME_CONTRACT.md` §2.1 P2 live-kernel boundary section correctly forbids OpenClaw from creating `RuntimeBootstrapRequest`, mutating `RuntimeBinding`, invoking the Lean Launcher or broker SDK, or approving capital authorization. This is consistent with the runbook's §9.6 and §9.2 forbidden paths.
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` §10 v1 decision item 7 and §8 are internally consistent with the runbook gate definitions.
- No raw secrets appear in any reviewed document.
- Fail-closed posture is stated explicitly in section headings, gap register, and hard invariants — not inferred.

---

## Notes (non-blocking)

- The runbook header (`Owner: Claude`, `Reviewer: Codex`) is a vestigial ownership line from P1-LIVE-PLAN-001; it does not affect the substantive review and is not a P2 scope item.
- `OPENCLAW_PRODUCTION_BROKER_ENABLED` flag name is referenced in OPENCLAW_RUNTIME_CONTRACT.md §2.1 as a deny-by-default gate; if this flag is ever introduced as a real env variable, its schema should be defined in the adapter config spec.

---

## Verdict

All three acceptance criteria pass. The three artifacts are internally consistent and aligned with their source policy files. The work is approved and returned to Codex for closeout.
