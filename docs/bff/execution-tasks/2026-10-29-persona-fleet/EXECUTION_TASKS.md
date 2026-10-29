# Persona Fleet / Agora: approved execution tasks

## Operator authorization and ownership

The operator approved the complete six-part plan in conversation
01a15322-2777-7553-aae8-e8de378eff9b on 2026-10-29 Asia/Taipei and explicitly
requested execution tasks for the existing supervisor and auto-workers.
This document is the input contract, not evidence that implementation or
deployment has happened. Chatbox authors the tasks and observes intake; native
workers implement, independent native reviewers review, and the final hosted
task owns deployment and actual acceptance. Do not spawn a second dispatcher.

Dependency graph (terminal source delivery is required):
PERSONA-FLEET-BFF-20261029 -> PERSONA-FLEET-FE-20261029
Both source tasks -> PERSONA-FLEET-DEV-ACCEPTANCE-20261029.

Source task DONE means reviewed, CI green, merged to the correct dev repository;
it does not mean the product is hosted. The final task declares the existing
hosted completion track pending and may finish only with real hosted evidence.
Preserve completed Pattern and financial work and existing Staging/Prod holds.

## Proven baseline and limits

Actual read-only browser evidence:
/tmp/persona-dev-diagnostic-20261028-Uqkye8/diagnosis.json and result.json.
Observed 2026-10-28T11:55Z: accepted BE18996eb6cc8577ef7171d2635d4812e43d60822f
and FE24534489030770f9e7a52cb893e97efa14b53cc4. Fleet API response times were
4934ms and3961ms; table visible5598ms and4982ms. Login/logout200. No product
writes or provider calls. Most delay was before first byte; per-component
cost share has NOT been measured and must not be invented.

The directory contained106 personas. page_size100 returned95 retired and5
visible rows and next_page_token100. Frontend never consumes that next token.
Two Agora Servants had no runtime/binding or routed strategy but were shown
paper_running, degraded, artificial paper-ledger identifiers and ranks103/104.
Current roles are stored in metadata.persona_class=agora_servant, with private
user scope and execution_authority=none. The frontend currently calls all
non-live/non-canary capital modes nonproduction, so it conflates paper and test.

Later read-only plan verification found accepted Root40772967418 with
BE4ed7e502e43c6b417e7a7e48a5517d746c6ba8c9 and the same FE2453448;
the scoped BFF/persona source was byte-identical to the measured baseline.
These are historical baselines, not deployment defaults. Refresh current dev,
hosted identities and real named environment variables when executing.

## Product behavior

1. Trading Persona Fleet includes genuine trading personas in research/draft,
   undeployed, paper, canary and live stages. Lack of runtime alone must not
   exclude a valid research persona or fabricate a running state.
2. Agora Servant is an advisory assistant surfaced in the existing Agora UI.
   Shared Persona Registry is retained. Its paper ceiling is an authority
   ceiling, not evidence of a running trading runtime or financial binding.
3. Test/acceptance data is a separate dimension from execution mode. A normal
   paper strategy is not test merely because its capital is simulated.
   Prefer existing explicit persona_class and traceable creation/provenance
   metadata. Resolve legacy records using existing canonical facts; names and
   ID regex alone are not authoritative. Ambiguous records remain marked
   unknown/to-confirm and reachable, not silently discarded or called regular.
4. Retired records remain retained and are fetched only when the user requests
   archived scope. No data migration, deletion, runtime activation or backfill.
5. Preserve current trading score/formula/admission/promotion policy. Exclude
   explicit nontrading assistants from trading cohorts and verify all affected
   ranking consumers. Do not simply renumber a visible page or recompute ranks
   only from page contents; record any intended cohort correction explicitly.
6. Tenant isolation and user-private assistant visibility remain enforced by
   existing identity/owner policy. No assistant capital/order/runtime authority.

## BFF task: PERSONA-FLEET-BFF-20261029

Single writer for the existing persona classification/list/ranking read path.
First measure owner reads, ranking, row projection and serialization using
existing diagnostics and simple scoped timings. Record real commands/results.

Extend the existing fleet endpoint with explicit trading-purpose scope,
data-scope and archived filters, using existing deployment_stage/state/search
and page_token/page_size wherever sufficient. Publish exact field names,
defaults, values, response envelope, examples and ordering in the task contract
before frontend implementation. Preserve other callers where parameters are
omitted; inventory affected consumers and avoid ambiguous changed defaults.
Add role/data-scope provenance to the existing row DTO as needed. Unknowns
remain explicit. Keep the existing owner surfaces and failure observations.

Filter identity/role/provenance/lifecycle/search as early as semantics allow;
use minimal global inputs for correct sorting, ranking, health and counts;
page before expensive per-row enrichment. Global totals and filtered totals
must describe the actual authorized population, not just returned rows.
Health-dependent filtering and ordering must remain correct across pages.
Do not imply that paging removes genuinely necessary whole-cohort ranking work.

Use existing request-scoped owner caches, batch reads and indices. Expensive
detail work should scale with visible page/active cohort rather than archived
history, and remote call counts must not grow once per persona. Profile before
choosing further changes; avoid a new cache service, background projector,
queue, generic registry, monitoring platform or authorization layer.

Agora has no trading rank/performance/derived ledger merely from paper ceiling;
its missing trading runtime is not a trading-health incident. Verify the
existing Agora read UI can continue displaying assistant status and real
capabilities without changing model, memory, consent or provisioning authority.

Tests must exercise real routes/projectors and responses: trading draft with
no runtime; regular paper; explicit test paper; canary/live; Agora servant;
legacy/unknown; archived; another tenant/user. Include >100 rows; deterministic
stable page traversal; search/detail beyond page one; totals and rank policy;
owner failures/partial results; no invented financial zero or healthy status.
Use existing budget tests with100 and1000 archived fixtures locally to expose
N+1 and redundant projection. Do not insert synthetic fixtures into dev stores.

Commit this exact approved task plan at
docs/bff/execution-tasks/2026-10-29-persona-fleet/EXECUTION_TASKS.md, plus a small
API_CONTRACT.md in that directory recording the final implemented contract
and actual local evidence. Existing tests and evidence.json are sufficient;
avoid a new test framework. Clean current-dev worktree, truthful Task-ID,
Reviewer and LLM-Agent trailers, focused tests, push, PR, required checks,
genuine independent frozen-head review, native merge/finalization.
This source task cannot deploy or perform business/provider calls.

## Frontend task: PERSONA-FLEET-FE-20261029

Repository ajoe734/execute-plans, separate checkout, PR base dev. Wait for
the BFF source task's terminal reviewed/merged contract; read its API_CONTRACT.
Do not copy frontend into Pantheon. Keep the fleet page and existing Agora
assistant surface, navigation and data adapters; no new assistant dashboard.

Fleet sends explicit trading/data/archive filters and page_size20; consumes
server page_info and totals, supplies page_token, and supports previous/next.
Use server classifications and purpose rather than inventing a second FE
classifier. Distinguish paper/canary/live from regular/test/unknown records.
Show unknown records through an explicit reachable filter. Show archive count
truthfully across all pages and lazy-load when requested; preserve original
deep links/bookmarks with a small explicit mapping where needed.

Reset pagination on filter/search changes; abort superseded requests; prevent
late responses from overwriting the selected scope. Include identity scope,
all filters and page in existing cache keys, preventing cross-user reuse.
Focused Persona links locate the authorized exact record independently of the
first page, including draft and unknown/test scopes with clear badges.

Differentiate initial loading, refreshing, true empty, filtered empty and
dependency error/partial states. Preserve last known facts with visible as-of
when appropriate, not stale data disguised as fresh or factual zero.
Absent runtime/metric/rank remains absent with an accurate reason.
Agora UI shows actual assistant identity/readiness/capabilities, not trading
paper_running/rank/ledger. Preserve the existing advisory interaction entry.

Focused UI/adapter/browser tests cover >100 mixed records across pages;
regular paper versus explicit test; Agora absent from fleet; archive counts;
direct target beyond100; filter race; unknown data; owner error/empty/loading;
responsive scroll and actual navigation. Run typecheck/build and required CI.
Do not start an interaction, call models, trade or deploy in this source task.
Use native branch/PR/review/merge and record exact frontend commit.

## Hosted task: PERSONA-FLEET-DEV-ACCEPTANCE-20261029

Wait for BOTH source tasks terminal reviewed/merged to dev. This is the sole
deployment/hosted acceptance owner for this task family; reviewers observe and
review evidence, not trigger another release. It is ordinary authorized dev
work, with no Stage/Prod or trading/capital/model authority.

Refresh current named GitHub dev variables and hosted manifest/version. Freeze
the reviewed exact FE/BFF pair from the source dependencies. Use only existing
Pantheon Nonprod Deploy and its FE gate/controller, gate-before-switch,
rollback-safe existing release entry and hosted identity verification.
Inspect active release ownership first; do not duplicate/cancel another root.
Record the unique actual root/gate/FE run IDs, official artifacts and hashes,
served pair, both source commits and observed times. If watchdog/Publisher
coordination is required by the original flow, preserve and restore its actual
prior state and receipt; do not add a gate/lock/issuer or second deployment path.

Read-only actual browser QA with existing admintest credentials kept private:
login/logout; fleet regular/test/unknown/archive filters; all page traversal;
target deep links; accurate totals and archived count; Agora assistant existing
read surface; no trading fields for servants; ordinary paper/draft semantics;
actual existing financial/runtime readbacks and missing metrics. Auth/session
and ownership checks use existing accounts and local regression where a second
hosted identity is unavailable; do not invent credentials or claim an unrun
hosted privacy test passed. Never submit model requests, trade or alter product
records to manufacture acceptance. Screenshots, network timings and sanitized
payloads preserve real owners/as-of/failure semantics.

Measure baseline and changed versions from the same client/VM/network and
comparable real dataset; do not redeploy an old baseline merely to benchmark.
Retain original two measured timings as historical baseline and collect fresh
pre-switch observations where available. Document dataset count/mix, network,
timing definition and workload. Target warm API p95<=1000ms over at least20
samples; cold API<=2000ms; usable table<=2500ms. Warm means repeated read under
the same served pair; cold means fresh browser/session/page entry without
restarting service or changing caches just to obtain a number. Include at
least3 fresh entries and distinguish login latency from authenticated page
navigation. First usable table means real rows or truthful successful empty,
not the skeleton. Report sorted samples and p95 method, not only fastest read.
Record pair before/after each batch; reject samples spanning a version switch.
Large archive scaling uses isolated local fixtures, never hosted data writes.

If any target or functional criterion fails, retain true evidence and use the
existing blocker/reopen process on the responsible source task. Do not hide a
failure with timeouts, removed rows, stale cache, invented totals or waived AC.
Preserve source completion separately from outstanding hosted acceptance.

Final evidence and concise reviewer report include before/after samples,
pagination coverage, role/data-state matrix, screenshots and actual pair,
all PR/merge/release references, required CI and independent review, original
financial correctness, owner errors/skips, rollback/restoration state and any
remaining gap. Record the existing hosted milestone DONE only with evidence;
then use native review and task DONE/archive. Evidence-only follow-up commits
are not a new product release and do not require redeploying an unchanged
runtime. Product delivery is never inferred from source tests/merge/supervisor.

## Shared execution boundaries

Use existing supervisor leases, current-dev clean task worktrees, configured
distinct identities and actual provider health. Never hand-edit canonical task
or queue JSON, reuse another worker lease, change provider auth/quota config,
forge credentials/evidence or bypass repository review and CI. No new MFA,
execution grant, product-development BFF route, dispatcher, general framework,
data migration, historical timestamp rewrite or financial/Loops/Pattern rerun.
Only the hosted task may use authorized dev deployment and read-only product
acceptance. Never print test passwords, tokens, cookies, secrets or raw auth
headers in evidence. Preserve tenant, secret and paper/live capital boundaries.
