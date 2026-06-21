# AG-XR-OPENAPI-003 Sidecar Review Packet

- Parent task: `AG-XR-OPENAPI-003` — Add `session_type` to `ServantSessionCreateRequest` in Agora v1.2 OpenAPI bundle
- Helper task: `AG-XR-OPENAPI-003-SIDECAR-REVIEW`
- Helper kind: `review_packet`
- Owner: `Claude`
- Reviewer: `Claude2`
- Prepared: `2026-06-21`
- Mutates canonical truth: `no`

This is a support artifact only. It does not implement the OpenAPI contract,
modify frozen Agora specs, edit capability manifests, or change runtime /
registry / governance behavior.

## Purpose

This packet records the evidence gathered during Claude's review of
`AG-XR-OPENAPI-003`. It surfaces hash verifications, schema integrity checks,
iron-rule audits, and CI check results in a structured form suitable for
parent-owner consumption and for `Claude2` to accept as the sidecar review
record.

## Parent Task Summary

| Field | Value |
|---|---|
| Task ID | AG-XR-OPENAPI-003 |
| LLM-Agent | Claude2 |
| Reviewer | Codex |
| Implementation commit | `9fd212ea` on `task/AG-XR-OPENAPI-003` |
| PR | [#2017](https://github.com/ajoe734/pantheon/pull/2017) — OPEN, base: `dev` |
| Key artifact | `services/control-plane/openapi/agora_v1_2.openapi.yaml` |
| Capability artifact | `services/control-plane/specs/agora/v3/capability_manifest_v1_2.json` |
| Bundle index | `services/control-plane/specs/agora/bundle_index.v1_2.json` |

## Sources Read During Review

| Source | Purpose |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar lifecycle, status command, and support-only workflow rules. |
| `ai-status.json` via `python3 scripts/ai_status.py show AG-XR-OPENAPI-003-SIDECAR-REVIEW` | Confirmed sidecar task is `in_progress`, owner Claude, reviewer Claude2, helper_parent AG-XR-OPENAPI-003. |
| `.orchestrator/task-briefs/ag_xr_openapi_003_sidecar_review.md` | Confirmed sidecar scope: review_packet only, no canonical truth changes. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md` | Design-closure document; C1 common envelope alignment referenced in commit. |
| `git show 9fd212ea --stat` | Implementation commit examined for scope — 3 files changed, 39 insertions, 7 deletions. |
| `git show 9fd212ea -- services/control-plane/openapi/agora_v1_2.openapi.yaml` | Full diff of OpenAPI changes reviewed. |
| `git show 9fd212ea -- services/control-plane/specs/agora/v3/capability_manifest_v1_2.json` | Full diff of capability manifest changes reviewed. |
| `git show 9fd212ea -- services/control-plane/specs/agora/bundle_index.v1_2.json` | Full diff of bundle index hash updates reviewed. |
| `gh pr view 2017 --json statusCheckRollup,reviews,baseRefName` | PR CI status; all 4 required checks confirmed passing. |
| `support/sidecars/AG-XR-OPENAPI-001/AG-XR-OPENAPI-001-SIDECAR-REVIEW.md` | Prior sidecar format reference. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## Review Verdict

**PASS** — all checks pass; frozen files untouched; hashes verified; CI green.
No iron rules violated. No blocking issues found.

## Change Summary

AG-XR-OPENAPI-003 makes three strictly additive edits to the v1.2 bundle.
No v1 or v1.1 frozen artifacts are touched.

### 1. `agora_v1_2.openapi.yaml`

- Added 6 lines to the info block documenting the `session_type` addition and
  its C1 common envelope alignment. Updated `x-extension-by` from
  `AG-XR-OPENAPI-002` to `AG-XR-OPENAPI-003`.
- Added `description` and authority-order note to `ServantSessionCreateRequest`.
- Added optional `session_type` field to `ServantSessionCreateRequest`:
  - `type: string`
  - `enum: [interactive, trainer, research_task]`
  - `default: interactive`
  - `additionalProperties: false` preserved.
  - Existing fields (`intent`, `strategy_ref`, `metadata`) unchanged.

### 2. `services/control-plane/specs/agora/v3/capability_manifest_v1_2.json`

- Updated `extension_by` from `AG-XR-OPENAPI-002` to `AG-XR-OPENAPI-003`.
- Updated top-level `description` to note the session_type addition.
- Extended `agora.servant.v1` capability entry:
  - Added `extended_by: AG-XR-OPENAPI-003`.
  - Added `session_type_contract` object (field, location, enum, default,
    optional, additionalProperties_unchanged, c1_common_envelope_aligned,
    strategy_dialogue_allows).
  - Added `authority_order` array stating v1.2 supersedes v1.1 definition.
- No other capabilities (`agora.workshop.v1`, `agora.dashboard.v2`,
  private-content capabilities) were changed.

### 3. `services/control-plane/specs/agora/bundle_index.v1_2.json`

- Updated SHA256 entries for the two changed files only:
  - `specs/agora/v3/capability_manifest_v1_2.json`
  - `openapi/agora_v1_2.openapi.yaml`
- All other 6 file entries and the `extends` block (pointing to v1.1 bundle)
  are unchanged.

## Hash Verification

All hashes independently verified by `sha256sum` on the files at commit `9fd212ea`.

| File | SHA256 in bundle_index.v1_2.json | Independently computed | Match |
|---|---|---|---|
| `openapi/agora_v1_2.openapi.yaml` | `5a8de5667869e17a662f34433e397a4140d68f038b1a76145d0d123d65774e66` | `5a8de5667869e17a662f34433e397a4140d68f038b1a76145d0d123d65774e66` | ✓ |
| `specs/agora/v3/capability_manifest_v1_2.json` | `de7bffc221ecebf268c9a3a5da4787c5aaaa5088ecb8bc4502cc1ed0a4722b33` | `de7bffc221ecebf268c9a3a5da4787c5aaaa5088ecb8bc4502cc1ed0a4722b33` | ✓ |
| `extends.bundle_index_sha256` (v1.1 pointer) | `5f875202966d1e373ab325b7107de8355798f1e3f55cdac2548aa74607a821ee` | `5f875202966d1e373ab325b7107de8355798f1e3f55cdac2548aa74607a821ee` | ✓ |

The 6 unchanged file entries in the bundle index carry forward from AG-XR-OPENAPI-002
without modification — consistent with a pure additive update.

## Iron Rules Audit

| Rule | Result |
|---|---|
| Frozen v1 files not modified (`agora_v1.openapi.yaml`, `bundle_index.json`) | ✓ verified by `git show 9fd212ea -- <frozen files>` producing no diff |
| Frozen v1.1 files not modified (`agora_v1_1.openapi.yaml`, `bundle_index.v1_1.json`) | ✓ same verification — no diff |
| `extends.bundle_index_sha256` in v1.2 index preserves the exact v1.1 bundle hash | ✓ `5f875202...` matches independently computed v1.1 hash |
| All v1 bundle entries in `bundle_index.json` unchanged — `scripts/agora_schema_bundle.py --verify` | ✓ 15 files OK |
| `additionalProperties: false` preserved on `ServantSessionCreateRequest` | ✓ confirmed via `yaml.safe_load` inspection |
| `execution_authority` on `agora.servant.v1` unchanged (no capability widening) | ✓ `execution_authority: none` unchanged |
| No new routes, operation ids, or schemas added | ✓ path count remains 33; only existing `ServantSessionCreateRequest` schema extended |
| session_type is optional with a safe default (`interactive`) | ✓ `default: interactive`; existing callers omitting the field continue unaffected |
| No broker order routing, capital binding, or RuntimeBinding writes introduced | ✓ structural: no new paths; existing path semantics unchanged |

## YAML Parsing Check

```python
import yaml
d = yaml.safe_load(open('services/control-plane/openapi/agora_v1_2.openapi.yaml'))
paths = list(d['paths'].keys())
# Result: 33 paths — unchanged from AG-XR-OPENAPI-002 baseline
```

Run against the file content at commit `9fd212ea`. Result: **OK, 33 paths**.

## CI Status (PR #2017)

| Check | Status | Conclusion |
|---|---|---|
| Commit trailers | COMPLETED | SUCCESS |
| Forward to orchestrator | COMPLETED | SUCCESS |
| Runtime mirror guard | COMPLETED | SUCCESS |
| Smoke acceptance | COMPLETED | SUCCESS |

All four required checks pass. PR is mergeable to `dev` from a CI perspective.

## Schema Field Inspection

`ServantSessionCreateRequest` at commit `9fd212ea`:

```yaml
ServantSessionCreateRequest:
  properties:
    intent: { type: string }
    strategy_ref: { type: string }
    session_type:
      type: string
      enum: [interactive, trainer, research_task]
      default: interactive
      description: "..."
    metadata: { type: object }
  additionalProperties: false
```

- `session_type` is new and optional (no `required` entry added).
- `strategy_dialogue_allows: [interactive, trainer]` is documented in the
  capability manifest, consistent with commit message.
- Backward compatibility is preserved: callers omitting `session_type` receive
  the `interactive` default.

## Dependency State After This Task

| Dependency | Status | Note |
|---|---|---|
| AG-XR-OPENAPI-001 (v1.1 bundle) | Done; merged PR #1839 | v1.1 frozen and immutable |
| AG-XR-OPENAPI-002 (v1.2 bundle) | Done; merged PR #1985 | v1.2 base bundle in dev |
| AG-XR-OPENAPI-003 (this parent) | PR #2017 open; CI green | session_type addition pending merge to dev |
| AG-BE-SW-001 (workshop implementation) | Blocked — depends on AG-XR-OPENAPI-002 merge | Unblocked once PR #2017 merges |

## Reviewer Questions for Claude2

| Question | Expected stance |
|---|---|
| Does this packet preserve the support-only boundary? | Approve only if no canonical specs, OpenAPI files, capability manifests, bundle indexes, runtime code, or registry/governance implementation were edited by this sidecar. |
| Are the hash entries in `bundle_index.v1_2.json` self-consistent? | Approve if the table above shows all three hash rows match. |
| Were frozen v1 and v1.1 files untouched? | Approve if `git show 9fd212ea -- <frozen files>` produces no diff for any of the four frozen paths. |
| Is `additionalProperties: false` preserved? | Approve if schema inspection confirms false (not absent or true). |
| Is the `session_type` field optional with safe default? | Approve if `default: interactive` is present and `session_type` is absent from any `required` list. |
| Is CI green on PR #2017? | Approve if all four status checks are SUCCESS. |

## Suggested Handoff

If this packet is acceptable, `Claude2` can treat it as the review evidence
summary for `AG-XR-OPENAPI-003-SIDECAR-REVIEW`.

Recommended handoff message:

```text
Review packet ready for AG-XR-OPENAPI-003: evidence summary and hash-verification
audit are in support/sidecars/AG-XR-OPENAPI-003/AG-XR-OPENAPI-003-SIDECAR-REVIEW.md.
The packet documents: session_type addition to ServantSessionCreateRequest (enum,
default, additionalProperties=false preserved); all 3 bundle hashes verified against
sha256sum; frozen v1/v1.1 files untouched; CI all 4 checks green on PR #2017;
no new routes, no capability widening, no iron rules violated.
```

## Verification Commands Run

```bash
git branch --show-current
git status --short
python3 scripts/ai_status.py show AG-XR-OPENAPI-003-SIDECAR-REVIEW
git show 9fd212ea --stat
git show 9fd212ea -- services/control-plane/openapi/agora_v1_2.openapi.yaml
git show 9fd212ea -- services/control-plane/specs/agora/v3/capability_manifest_v1_2.json
git show 9fd212ea -- services/control-plane/specs/agora/bundle_index.v1_2.json
git show 9fd212ea -- services/control-plane/openapi/agora_v1.openapi.yaml \
  services/control-plane/openapi/agora_v1_1.openapi.yaml \
  services/control-plane/specs/agora/bundle_index.json \
  services/control-plane/specs/agora/bundle_index.v1_1.json
git show 9fd212ea:services/control-plane/openapi/agora_v1_2.openapi.yaml | sha256sum
git show 9fd212ea:services/control-plane/specs/agora/v3/capability_manifest_v1_2.json | sha256sum
git show 9fd212ea:services/control-plane/specs/agora/bundle_index.v1_2.json
sha256sum services/control-plane/specs/agora/bundle_index.v1_1.json
python3 scripts/agora_schema_bundle.py --verify
git show 9fd212ea:services/control-plane/openapi/agora_v1_2.openapi.yaml | python3 -c "
  import yaml,sys; d=yaml.safe_load(sys.stdin)
  schemas = d['components']['schemas']
  req = schemas['ServantSessionCreateRequest']
  print(list(req['properties'].keys()), req['additionalProperties'])
  print(req['properties']['session_type'])
"
gh pr view 2017 --json statusCheckRollup,baseRefName
```

## Sidecar Completion Criteria

This sidecar is ready for review when:

- this review packet exists at the declared artifact path;
- it records verification evidence against the parent acceptance criteria;
- it preserves the support-only boundary (no canonical truth changes);
- it is handed off to `Claude2` for review and possible absorption by the parent owner.
