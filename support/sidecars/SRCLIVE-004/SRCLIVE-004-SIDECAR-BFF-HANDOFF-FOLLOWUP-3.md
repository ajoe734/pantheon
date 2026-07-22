# SRCLIVE-004 BFF and Frontend Handoff Follow-up 3

**Parent Task**: `SRCLIVE-004` - overlay regression tests and three-persona
live readback acceptance
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude2`
**Parent Status at packet time**: `review_approved`
**Sidecar Task**: `SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-06-28`
**Mutates canonical**: `no`

This is a support artifact only. It does not change canonical truth, BFF
runtime code, source-ingest code, registry/governance behavior, frontend code,
or parent task acceptance. Parent ownership and review decide whether to absorb
these notes into SRCLIVE-004 closeout or later frontend/operator work.

---

## 1. Scope

FOLLOWUP-2 captured the state before the parent SRCLIVE-004 implementation and
review completed. That snapshot is now stale.

This follow-up records the post-review handoff facts:

1. SRCLIVE-004 is active `review_approved`; owner closeout remains with Codex.
2. SRCLIVE-001 is archived `done` with `terminal_outcome: superseded` by
   SRCLIVE-004 after TW live readback evidence was consolidated.
3. SRCLIVE-002 and SRCLIVE-003 remain archived `done`.
4. SRCLIVE-004 artifacts now exist in the repo:
   `services/control-plane/bff/test_srclive_overlay_contract.py` and
   `scripts/verify_srclive_readback.py`.
5. The latest live verifier output succeeds against the dev BFF.
6. The current US BFF chip baseline no longer matches FOLLOWUP-2 exactly:
   `persona-us-equity` projects a `yahoo` chip, while Stooq is tolerated as a
   source-only or optional provider path by PR #2554.

Non-goals:

- no edits to SRCLIVE-004 implementation artifacts;
- no edits to `_SOURCE_PROVIDER_CONNECTOR_CANDIDATES`;
- no edits to `read_store.py`;
- no edits to source-ingest adapters, active-universe rules, BFF routes,
  frontend code, registry/governance implementation, or canonical docs;
- no approval or rejection of parent task `SRCLIVE-004`;
- no claim that this sidecar replaces the missing review file artifact noted
  below.

I did not read `current-work.md` or the full `ai-activity-log.jsonl`.

---

## 2. Source References

| File or surface | Why it matters |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001` | Shows TW dependency archived `done`, `terminal_outcome: superseded`, and superseded by SRCLIVE-004 |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | Shows US source wiring archived `done` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | Shows CoinGecko wiring archived `done` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004` | Shows parent active `review_approved`, reviewer notes, and artifact targets |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Confirms this sidecar is active `in_progress`, owner `Codex2`, reviewer `Claude`, support artifact path |
| `services/control-plane/bff/test_srclive_overlay_contract.py` | Parent overlay guardrail test file now present |
| `scripts/verify_srclive_readback.py` | Parent live readback verifier now present |
| `services/control-plane/bff/main.py` | Current provider-to-connector map and source-health overlay logic |
| `services/control-plane/bff/read_store.py` | Current persona data-source rows; US now includes `yahoo` rather than a direct `stooq` row |
| `support/sidecars/SRCLIVE-004/SRCLIVE-004-SIDECAR-BFF-HANDOFF.md` | Initial SRCLIVE-004 BFF/frontend handoff |
| `support/sidecars/SRCLIVE-004/SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Prior snapshot; useful history but stale for parent closeout |

Observed git history for parent implementation:

| PR / commit | Parent delta |
|---|---|
| PR #2539 / `f8ffc8e3` | Added SRCLIVE-004 verifier, overlay contract tests, US public-source fetch repair, and runbook updates |
| PR #2548 / `b7d05568` | Accepted source-ingest job parameters used by the live readback path |
| PR #2554 / `6124a70b` | Adjusted verifier to tolerate Stooq as source-only/optional when the BFF projects the public price chip as Yahoo |

Review artifact caveat: `ai-status.json` reports parent review file
`.orchestrator/reviews/srclive_004_review.md`, but that file is not present in
this checkout at packet time. The review notes are visible in
`AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004`. Parent closeout should
not treat this sidecar as a replacement for the missing review file if the
review artifact is required for final archival.

---

## 3. Current Task Snapshot

| Task | Current state | SRCLIVE-004 implication |
|---|---|---|
| `SRCLIVE-001` | archived `done`, `terminal_outcome: superseded`, `superseded_by: SRCLIVE-004` | FOLLOWUP-2's TW blocker is stale; TW live readback is now folded into parent SRCLIVE-004 evidence |
| `SRCLIVE-002` | archived `done`; US wiring and credential-gated behavior accepted | Parent verifier can rely on merged US map/read-store behavior, while respecting later Yahoo/Stooq adjustment |
| `SRCLIVE-003` | archived `done`; CoinGecko connector and BFF mapping accepted | Parent verifier can assert CoinGecko `read_ok` through BFF source-ingest overlay |
| `SRCLIVE-004` | active `review_approved`; owner `Codex`, reviewer `Claude2` | Parent owner still needs closeout, PR/merge accounting, and `done` transition |
| `SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | active `in_progress`; owner `Codex2`, reviewer `Claude` | This packet should go to Claude review as support-only material |

---

## 4. Parent Artifacts Now Landed

`services/control-plane/bff/test_srclive_overlay_contract.py` currently provides
five focused guardrails:

| Test area | Guardrail |
|---|---|
| all-green TW overlay | TW 5/5 source-backed readback promotes the summary badge to `live_readback_ok` only when TWSE/TPEx, MOPS, and FinMind source-ingest health are `ok` |
| TW official sources | TWSE and TPEx share `tw-twse-tpex-official-market`; MOPS uses `tw-mops-official-disclosures`; all flip only from source-ingest health |
| missing health | Missing source-ingest health preserves static `read_unavailable` and `credential_unavailable`; no fake green |
| US public/key-gated mix | SEC EDGAR, FINRA, and FRED can flip to `read_ok` from health; Polygon and Alpha Vantage preserve `credential_unavailable` plus `secret_ref` unless health is actually `ok` |
| crypto | CoinGecko maps to `crypto-coingecko-spot` and flips to `read_ok` only from source-ingest health |

`scripts/verify_srclive_readback.py` is read-only. It calls:

```bash
GET /bff/management/persona-fleet
```

and optionally:

```bash
GET /api/source-ingest/health-usage-snapshot
```

when `--source-ingest-base` is provided. The BFF check remains the pass/fail
surface for frontend/operator acceptance; source-ingest direct access is
diagnostic only.

---

## 5. Current Live Verifier Result

Command:

```bash
python3 scripts/verify_srclive_readback.py --json
```

Result at packet time: pass.

Observed BFF summary:

| Persona | Provider statuses observed |
|---|---|
| `persona-tw-equity` | `shioaji=read_ok`, `twse=read_ok`, `tpex=read_ok`, `mops=read_ok`, `finmind=read_ok`; `source_health_source=source_ingest` |
| `persona-us-equity` | `ibkr=read_ok`, `sec_edgar=read_ok`, `finra=read_ok`, `fred=read_ok`, `polygon=credential_unavailable`, `alphavantage=credential_unavailable`, `yahoo=read_unavailable`; `source_health_source=source_ingest` |
| `persona-crypto` | `coingecko=read_ok`, `kraken=datasource_smoke_ok`; `source_health_source=source_ingest` |

Important US delta: FOLLOWUP-2 said the frontend should expect a `stooq` chip.
Current `read_store.py` projects `yahoo` as the US public daily OHLCV row, with
reason text explaining that Yahoo replaces blocked Stooq CSV. Current
`main.py` maps:

| Provider key | Candidate connector ids |
|---|---|
| `yahoo` | `us-yahoo-daily-ohlcv` |
| `stooq` | `us-yahoo-daily-ohlcv`, `us-stooq-daily-ohlcv` |

PR #2554 updated the verifier so `stooq` is optional: if BFF projects it, it
must be `read_ok`; if BFF projects `yahoo` instead, absence of `stooq` does not
fail the parent readback. Frontend work should therefore avoid hardcoding a
Stooq row. It should render the provider rows BFF returns and rely on
source-health overlay evidence for green state.

---

## 6. Frontend and Operator Handoff

| Rule | Required behavior |
|---|---|
| Transport | Browser/operator UI reads SRCLIVE status through BFF, especially `/bff/management/persona-fleet` |
| Direct source-ingest | Direct `/api/source-ingest/*` calls remain backend diagnostics, not a browser contract |
| Green state | Source-backed green remains literal `read_ok` plus BFF-projected source-ingest overlay evidence |
| Missing health | Missing or degraded source-ingest health must remove green state on the next fresh BFF response |
| US public source | Render BFF provider rows as returned; do not hardcode Stooq as the visible row when BFF projects Yahoo |
| Credential states | `credential_unavailable` remains action-required and should preserve reason/`secret_ref` metadata |
| Crypto authority | CoinGecko green is readback health only; it does not imply broker, order, capital, RuntimeBinding, or live trading authority |
| Kraken | Existing `kraken=datasource_smoke_ok` is informational and should not be promoted to order authority |
| Cache | Cached frontend state cannot preserve a green provider after BFF removes live health evidence |

Parent closeout should preserve this boundary: BFF is the operator-facing truth
projection; source-ingest is an upstream diagnostic; frontend rows are rendered
from BFF response shape, not local provider denominators.

---

## 7. Parent Closeout Notes

For Codex as parent owner, the remaining closeout work is procedural rather than
sidecar implementation:

1. Confirm SRCLIVE-004 branch/PR history is merged into `dev` through the parent
   PRs noted above.
2. Preserve the `review_approved` status evidence from
   `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004`.
3. Resolve or explicitly account for the missing
   `.orchestrator/reviews/srclive_004_review.md` file if closeout archival
   requires it.
4. Re-run focused verification if `dev` advances before parent `done`:

   ```bash
   python3 -m pytest services/control-plane/bff/test_srclive_overlay_contract.py -q
   python3 scripts/verify_srclive_readback.py --json
   ```

5. Use `AI_NAME=Codex ./scripts/ai-status.sh done SRCLIVE-004 "<message>"` only
   after the repository closeout rules are satisfied.

This sidecar does not move parent SRCLIVE-004 to `done`.

---

## 8. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if this packet and the task-scoped brief are the only task-owned changes |
| Canonical/runtime untouched | PASS if no L1 docs, BFF runtime, source-ingest, registry/governance, or frontend code changed |
| Latest state reflected | PASS if packet treats SRCLIVE-004 as `review_approved`, SRCLIVE-001 as superseded/done, and SRCLIVE-002/003 as done |
| Parent artifacts described | PASS if packet names the landed overlay test and verifier script without modifying them |
| Yahoo/Stooq delta preserved | PASS if packet does not require a visible Stooq chip when BFF currently projects Yahoo |
| Live verifier result recorded | PASS if packet records the current three-persona BFF readback summary |
| Frontend boundary preserved | PASS if browser remains behind BFF and chips remain read-only truth |
| Missing review artifact caveat | PASS if packet records that the status-referenced review file is not present in this checkout |

---

## 9. Verification Performed

| Command | Result |
|---|---|
| `git status -sb` | Correct branch `task/SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`; only task-scoped brief was dirty before packet creation |
| `git fetch origin dev` | Updated local `origin/dev` |
| `git merge --ff-only origin/dev` | Fast-forwarded task branch to `f7fb9ac0` before writing this packet |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-001` | Archived `done`, `terminal_outcome: superseded`, `superseded_by: SRCLIVE-004` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-002` | Archived `done` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-003` | Archived `done` |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004` | Active `review_approved`; review notes present in status; review file path not present in checkout |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Active `in_progress`; owner `Codex2`; reviewer `Claude`; artifact path matches this packet |
| `rg` / `sed` over `services/control-plane/bff/main.py` and `read_store.py` | Confirmed current Yahoo/Stooq provider mapping and US read-store row shape |
| `python3 -m pytest services/control-plane/bff/test_srclive_overlay_contract.py -q` | `5 passed, 4 warnings` |
| `python3 scripts/verify_srclive_readback.py --json` | Passed; observed TW 5/5 read_ok, US read_ok/credential mix plus `yahoo=read_unavailable`, and crypto `coingecko=read_ok` |

No runtime, canonical, BFF implementation, source-ingest implementation,
registry/governance, or frontend code was changed by this sidecar.

---

## 10. Handoff Status

This packet is ready for `Claude` review as support-only material. If the
reviewer agrees with the checklist, suggested review command:

```bash
AI_NAME=Claude \
REVIEW_FILE=support/sidecars/SRCLIVE-004/SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
REVIEW_NOTES_ZH="審查通過：FOLLOWUP-3 正確反映 SRCLIVE-004 post-review BFF/frontend handoff；父任務已 review_approved，SRCLIVE-001 已由 SRCLIVE-004 supersede/done，SRCLIVE-002/003 已 done，packet 保持 support-only 且未改 canonical/runtime/source-ingest/frontend。||後續：parent owner Codex 可用此 packet 輔助 closeout，但仍需處理 SRCLIVE-004 自身 PR/merge/done 與缺失 review file artifact 的收尾。" \
./scripts/ai-status.sh approve SRCLIVE-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
"Sidecar FOLLOWUP-3 approved; support-only SRCLIVE-004 post-review BFF/frontend handoff returned to Codex2 for closeout."
```

If factual drift appears, request a narrow packet correction instead of
changing canonical, BFF, source-ingest, governance, registry, or frontend files
from this sidecar.
