# BFF API GAP — Delta-v3 Dispatch Spec (2026-05-25)

Source audit: `execute-plans/.lovable/audits/bff-backend-gap-2026-05-25-delta-v3.md`
（Lovable re-probe after BE team reported "BFF development complete" 2026-05-25）

Baseline: `docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md`
（22 tasks dispatched 2026-05-24 — 21 of 22 archived done, but 1 critical
deploy task blocked → live unchanged for 24+ hours）

Dispatch sprint: `2026-05-25-pantheon-bff-p0-delta-v3`
Dispatcher script: `scripts/dispatch_bff_gap_2026-05-25_delta_v3.py`

---

## 1. Root cause from v2 (what went wrong)

v2 dispatcher assigned `OPS-BFF-LUPIN-DEV-REDEPLOY-20260524` to Gemini2.
Gemini2 claimed the task 2026-05-24T14:35Z, attempted deploy, **failed at
14:52Z due to missing GCP IAM permission `compute.instances.get` on the
lupin project**, marked `blocked`. Chair-reviews flagged it twice the same
day; task was subsequently cleaned up from `ai-status.json` without ever
producing a deploy.

Result: 22 of 24 §8/§9 routes are merged into `services/control-plane/bff/main.py`
on `origin/dev`, CORS fix is in dev (commit `73a365fb`), envelope shape fix
is in dev, but **none of it is on the live `pantheon-lupin-dev-bff` because
the image was never rebuilt and rolled out**. Lovable's v3 audit therefore
sees essentially the same surface as v2.

Net forward progress from v2: 1 endpoint (`POST /bff/approvals/batch-decide`
went from 404 → 200, which was already-deployed in a prior cycle).

## 2. v3 gap analysis

Three buckets after grep against `origin/dev:services/control-plane/bff/main.py`:

### Bucket A — Pure deploy-lag, code already on dev (24 paths + 2 infra)
- 14 PM-Live §8 routes (cockpit, persona-league/*, strategy-allocation,
  capital-flow, risk-radar, incident-timeline, governance-ledger,
  cost-attribution, sentinel-pulse, loop-throughput, hiq-backlog,
  intervention-stream)
- 10 PM-12 §9 routes (quarterly-ranking + /drilldown, performance-attribution
  + by-{persona,strategy,pool}, portfolio-book + /{positions,exposure},
  board-pack)
- CORS preflight fix (commit `73a365fb`)
- Error envelope shape (strip `detail` wrapper + `meta.correlationId`)
- `GET /bff/command-confirmations/{token}` (main.py line ~31093)

All single-fix via redeploy.

### Bucket B — Real code gap (1 new finding from v3)
- **ErrorCode enum misalignment** with Pack D §D21 canonical 26 codes.
  Audit hit: `GET /bff/strategies/__nonexistent__` returns
  `error.code = "OBJECT_NOT_FOUND"` but Pack D §D21 requires
  `"RESOURCE_NOT_FOUND"`. Likely many of the other 25 codes also have
  legacy non-Pack-D names. `services/control-plane/bff/main.py:311`:
  `404: ErrorCode.OBJECT_NOT_FOUND.value` is one observable symptom.

### Bucket C — Naming canonical decision (5 FE/BE pairs)
- FE `paths.mgmt*` PM-9 builders → `/bff/management/persona-fleet`,
  `/human-inbox`, `/trading-pulse`, `/evolution-journal`, `/evidence`
  (BE has these but FE may be using different aliases)
- FE `paths.mgmtPortfolioHoldings` / `mgmtPortfolioPools` (PM-12) → BE
  `/positions` / `/exposure` (canonical name needs confirmation)
- 12 snake_case duplicate families (P3 advisory, e.g.
  `/bff/personas/{id}` vs `/bff/personas/{persona_id}`)

Needs decision doc (canonical name + which alias to keep / deprecate) before
any code change. Some may resolve via "BE already has both, FE just needs
to use canonical".

---

## 3. Three task dispatch

### 3.1 `OPS-BFF-LUPIN-DEV-REDEPLOY-20260525` — Codex / Claude

Owner: **Codex** (reassigned from Gemini2 in v2; user explicit instruction)
Phase: `Sprint BFF-DELTA-V3 / EPIC-BFF-DELTA-V3-INFRA`

**Acceptance**:
1. Investigate why Gemini2 lacked `compute.instances.get` IAM on lupin
   project; document either: (a) Codex can self-grant via project owner
   creds, (b) Codex shells out to operator's existing gcloud, or
   (c) blocker is real and needs operator manual action — in which case
   produce exact `gcloud projects add-iam-policy-binding` command and
   handoff doc; do not mark task done until live BFF actually reflects
   `origin/dev` HEAD
2. Rebuild lupin dev BFF image from `pantheon@origin/dev` HEAD
   (current SHA at dispatch: `a23a9dd5`)
3. Push image + roll out service; new pod ready
4. CORS preflight from 4 Lovable origins to `/bff/me` returns 204 with
   full ACAO/ACAH/ACAM/ACEH headers
5. `curl -H "Authorization: Bearer pantheon-dev-browser:reviewer" <DEV_BFF>/<path>`
   returns 200 for at least these 8 paths (mix of v2 and v3 audit hits):
   - `POST /bff/approvals/batch-decide`
   - `GET /bff/command-confirmations/{token}`
   - `GET /bff/management/cockpit`
   - `GET /bff/management/persona-league/rankings`
   - `GET /bff/management/persona-league/movers`
   - `GET /bff/management/quarterly-ranking?quarter=2026-Q2`
   - `GET /bff/management/performance-attribution`
   - `GET /bff/management/portfolio-book`
6. Evidence at `support/evidence/bff-delta-v3-20260525/redeploy-curl-results.md`
   committed before status transition to `done`

### 3.2 `BFF-INFRA-ERRORCODE-PACKD-001` — Codex / Claude

Owner: **Codex**
Phase: `Sprint BFF-DELTA-V3 / EPIC-BFF-DELTA-V3-INFRA`

**Acceptance**:
1. Audit `services/control-plane/bff/main.py` `ErrorCode` enum against
   Pack D §D21 canonical 26 codes:
   `RESOURCE_NOT_FOUND`, `AUTH_REQUIRED`, `AUTH_EXPIRED`, `FORBIDDEN`,
   `RATE_LIMITED`, `VALIDATION_FAILED`, `BUSINESS_RULE_VIOLATION`,
   `IDEMPOTENCY_CONFLICT`, `PRECONDITION_FAILED`, `CONFIRMATION_REQUIRED`,
   `TWO_MAN_SIGNATURE_REQUIRED`, `HUMAN_GATE_PENDING`,
   `HUMAN_GATE_REJECTED`, `HUMAN_GATE_EXPIRED`, `RESOURCE_CONFLICT`,
   `OPERATION_NOT_ALLOWED`, `DEPENDENCY_UNAVAILABLE`,
   `UPSTREAM_TIMEOUT`, `UPSTREAM_ERROR`, `INTERNAL_ERROR`,
   `NOT_IMPLEMENTED`, `MAINTENANCE_MODE`, `KILL_SWITCH_ACTIVE`,
   `SAFE_MODE_ACTIVE`, `DEGRADED_READ_ONLY`, `REQUEST_TOO_LARGE`
2. Rename `OBJECT_NOT_FOUND` → `RESOURCE_NOT_FOUND` and align all other
   non-canonical names (full list documented in commit message)
3. Status-code mapping table (`_HTTP_STATUS_TO_ERROR_CODE` or equivalent)
   updated; 404 → `RESOURCE_NOT_FOUND`
4. Contract test `services/control-plane/bff/test_bff_error_envelope_shape.py`
   extended to assert exactly the Pack D §D21 26-code allowlist; any code
   not in allowlist fails the test
5. Verified live (after `OPS-BFF-LUPIN-DEV-REDEPLOY-20260525` completes):
   `GET /bff/strategies/__nonexistent__` returns
   `error.code = "RESOURCE_NOT_FOUND"`

### 3.3 `OPS-DOC-BFF-NAMING-CANONICAL-001` — Claude / Codex

Owner: **Claude**
Phase: `Sprint BFF-DELTA-V3 / EPIC-BFF-DELTA-V3-INFRA`

**Acceptance**:
1. Decision doc at `docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/CANONICAL_PATH_NAMING.md`
2. For each of the 5 FE/BE naming pairs from v3 audit, decide which is
   canonical and document migration path:
   - persona-fleet vs persona-league (overlap)
   - human-inbox / trading-pulse / evolution-journal / evidence (FE
     PM-9 builders that may map to BE Mgmt §8 routes under different names)
   - portfolio-book/holdings vs /positions
   - portfolio-book/pools vs /exposure
3. For each of the 12 snake_case duplicate families (P3 advisory),
   recommend keep / deprecate / both (decision is non-blocking for v3
   sprint close)
4. Cross-reference each decision to Pack D §D2 path naming convention
5. Update `execute-plans/src/lib/bff-v1/paths.ts` migration ticket
   if any FE-side path change is needed (separate follow-up task, not
   in scope here)

---

## 4. Sprint metadata

```
Sprint:        2026-05-25-pantheon-bff-p0-delta-v3
Started:       2026-05-25T00:00:00Z
EPIC:          EPIC-BFF-DELTA-V3-INFRA (all 3 tasks)
Total tasks:   3
Owner mix:     2 Codex / 1 Claude
Reviewer mix:  2 Claude / 1 Codex
```

## 5. Dependencies

```
OPS-BFF-LUPIN-DEV-REDEPLOY-20260525   (no upstream — unblock first)
                |
                ├──→  BFF-INFRA-ERRORCODE-PACKD-001 (can write code in parallel
                │     but live verification requires redeploy)
                │
                └──→  v3 audit re-probe (post-redeploy curl from operator
                      or new audit from Lovable)

OPS-DOC-BFF-NAMING-CANONICAL-001  (independent; pure docs/decision)
```

## 6. Babysit protocol (lesson from v2)

After dispatcher runs, the operator must verify within 4 hours:
1. Has the redeploy task been claimed by an agent? (`grep claim
   ai-activity-log.jsonl | grep REDEPLOY-20260525`)
2. If claimed, is it running or blocked?
3. If blocked, what's the unblock action? — escalate to operator immediately
4. **Do not mark v3 sprint "done" until live BFF curls verify the 8 paths
   in §3.1 acceptance #5 actually return 200**. ai-status `done` is not
   the same as live-verified.

---

## 7. Source audit excerpt

For audit trail, the v3 audit summary table (from Lovable):

| Bucket                       | 2026-05-24 | 2026-05-25 |
|------------------------------|------------|------------|
| Canonical paths implemented  | ~62 / 87   | ~63 / 87   |
| Still missing (canonical)    | 26         | 27         |
| Blockers (CORS / envelope)   | 2          | 2 (unchanged) |
| Schema deviations            | 1          | 2 (envelope + ErrorCode) |
| BE-added non-canonical paths | —          | 160+ (SSE cluster, activity/audit/skills detail) |

Net forward: +1 endpoint, 2 P0 blockers unchanged.
