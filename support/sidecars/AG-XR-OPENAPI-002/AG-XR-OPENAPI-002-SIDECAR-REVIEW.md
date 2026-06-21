# AG-XR-OPENAPI-002 Sidecar Review Packet

- Parent task: `AG-XR-OPENAPI-002` - Agora v1.2 OpenAPI/capability/schema bundle
- Helper task: `AG-XR-OPENAPI-002-SIDECAR-REVIEW`
- Helper kind: `review_packet`
- Owner: `Codex2`
- Reviewer: `Codex`
- Prepared: `2026-06-21`
- Mutates canonical truth: `no`

This is a support artifact only. It does not modify L1 canonical truth, core
contract truth, runtime code, registry behavior, governance behavior, or any
Agora schema/OpenAPI/bundle implementation file.

## Purpose

This packet summarizes review evidence for `AG-XR-OPENAPI-002` so the assigned
reviewer can quickly decide whether the parent-owner should absorb the v1.2
contract bundle into the mainline. The sidecar only records evidence and handoff
checks.

## Status And Handoff Note

`AI_NAME=Codex2 ./scripts/ai-status.sh start AG-XR-OPENAPI-002-SIDECAR-REVIEW
"Starting sidecar review packet and evidence summary."` was run before packet
work. In this worker environment, `PANTHEON_STATUS_ROOT` points to
`/home/lupin/code/pantheon`; no local `ai-status.json` diff is part of this
support commit.

After this packet is committed and pushed, handoff should target reviewer
`Codex` with this artifact as the review surface.

## Parent Implementation Surface

Parent implementation commit inspected:
`f7e0b2b990524ff1e677f0ffcf5dd38ccd96a66b`
(`AG-XR-OPENAPI-002: add Agora v1.2 contract bundle`).

Changed parent files:

| File | Parent change | Review relevance |
|---|---:|---|
| `scripts/test_agora_v1_2_bundle.py` | Modified | Focused bundle, OpenAPI, manifest regression tests. |
| `services/control-plane/openapi/agora_v1_2.openapi.yaml` | Modified | Additive v1.2 OpenAPI extension over v1.1. |
| `services/control-plane/specs/agora/bundle_index.v1_2.json` | Modified | v1.2 hash manifest and v1.1 byte-chain extension. |
| `services/control-plane/specs/agora/v3/capability_manifest_v1_2.json` | Added | v1.2 capability authority and privacy/lifecycle contract. |

The parent commit did not edit the frozen v1 bundle files, the v1.1 OpenAPI, the
v1.1 capability manifest, runtime routers, or registry/governance code.

## Sources Read

| Source | Purpose |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar and collaboration boundaries. |
| `.orchestrator/task-briefs/ag_xr_openapi_002_sidecar_review.md` | Task-scoped assignment and support-only mandate. |
| `.orchestrator/skills/worker-anchor-commit.md` | Commit discipline for support artifacts. |
| `.orchestrator/skills/task-closeout-finalization.md` | Review handoff and closeout boundary. |
| `ai-status.json` | Local task board snapshot; this generated sidecar was not present in the local checkout copy. |
| `scripts/test_agora_v1_2_bundle.py` | Focused v1.2 regression evidence. |
| `services/control-plane/specs/agora/bundle_index.v1_2.json` | Exact v1.2 file hashes and v1.1 extension hash. |
| `services/control-plane/specs/agora/v3/capability_manifest_v1_2.json` | Capability authority, privacy, lifecycle, strategy-ref, and error contract. |
| `services/control-plane/openapi/agora_v1_2.openapi.yaml` | OpenAPI structure, route coverage, schemas, headers, and safety notes. |
| `support/sidecars/AG-XR-OPENAPI-001/AG-XR-OPENAPI-001-SIDECAR-REVIEW.md` | Prior packet format and v1.1 review comparison. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Evidence Summary

| Evidence | Result |
|---|---|
| v1.2 bundle extends exact v1.1 bytes | PASS. `bundle_index.v1_2.json` records `bundle_index.v1_1.json` sha256 `5f875202966d1e373ab325b7107de8355798f1e3f55cdac2548aa74607a821ee`. |
| v1.2 file hashes match exact bytes | PASS. `pytest -q scripts/test_agora_v1_2_bundle.py` passed. |
| v1 frozen bundle remains intact | PASS. `python3 scripts/agora_schema_bundle.py --verify` passed all 15 frozen v1 entries. |
| v1.2 OpenAPI parses | PASS. YAML parsed to 33 paths and 35 operations. |
| v1.2 JSON artifacts parse | PASS. `python3 -m json.tool` passed for `bundle_index.v1_2.json` and `capability_manifest_v1_2.json`. |
| Status lifecycle correction | PASS. v1.2 limits canonical workshop statuses to `open`, `in_review`, `concluded`, `archived`; `active` is only `status_group=active`. |
| Private content contract | PASS. Browser requests do not submit `private_content_ref`; owner projections may include decrypted content; management projections exclude raw content and private refs. |
| Strategy reference contract | PASS. v1.2 uses nested `strategy_ref` with `strategy_id` and `strategy_spec_registry_id`; legacy `strategy_spec_ref` is deprecated. |
| Error-code closure | PASS. The Section 9 error set is asserted by focused tests and recorded in the v1.2 manifest. |
| Runtime/capital safety | PASS for contract surface. OpenAPI states adapter rejection for runtime-binding, broker-order, and capital-binding capability snapshots; no route binds capital or places broker orders. |

## Route Coverage Snapshot

The parsed v1.2 OpenAPI contains 33 paths and 35 operations:

| Family | Expected scope | Observed |
|---|---:|---:|
| `agora.servant.v1` BFF routes | 8 | 8 |
| `agora.workshop.v1` BFF routes | 13 | 13 |
| `agora.dashboard.v2` BFF routes | 11 | 11 |
| OpenClaw adapter internal routes | 3 | 3 |
| Total operations | 35 | 35 |

Workshop v1.2 routes preserve the v1.1 route family and tighten the workshop
aggregate semantics: create/list/get, messages, events, completeness, versions,
version selection, research runs, consultations, conclude, and stream.

## Key Contract Checks For Reviewer

| Area | Reviewer check |
|---|---|
| Additive bundle chain | Confirm `bundle_index.v1_2.json` extends `bundle_index.v1_1.json`, not `bundle_index.json` directly. |
| Frozen-file boundary | Confirm no v1/v1.1 files are modified by this sidecar packet or by the parent beyond the new v1.2 bundle surface. |
| Lifecycle semantics | Confirm `status=active` is not a v1.2 lifecycle value; clients should use `status_group=active` for `open + in_review`. |
| Private content | Confirm browser-submitted payloads omit `private_content_ref`; the server creates private refs after encrypted write and redaction. |
| Projection split | Confirm owner event projections and management projections have distinct schemas, with management seeing only `redacted_summary`. |
| Strategy Registry reference | Confirm v1.2 stores pointers only and does not copy StrategySpec JSON into workshop rows. |
| Error semantics | Confirm the Section 9 error-code set is present in both OpenAPI and manifest. |
| Capital/runtime safety | Confirm adapter capability snapshots reject runtime-binding, broker-order, and capital-binding capability classes. |

## Non-Blocking Observation

The live BFF capability route in the current codebase has historically loaded
the frozen v1 capability manifest. This packet does not change runtime readback.
If downstream gates require live discovery of the v1.2 manifest, that should be
a separate runtime/readback follow-up owned outside this sidecar.

## Verification Run

Commands run from this task worktree:

```bash
python3 scripts/test_agora_v1_2_bundle.py
pytest -q scripts/test_agora_v1_2_bundle.py
python3 scripts/agora_schema_bundle.py --verify
python3 -m json.tool services/control-plane/specs/agora/bundle_index.v1_2.json
python3 -m json.tool services/control-plane/specs/agora/v3/capability_manifest_v1_2.json
sha256sum services/control-plane/specs/agora/bundle_index.v1_1.json services/control-plane/specs/agora/bundle_index.v1_2.json services/control-plane/specs/agora/v3/capability_manifest_v1_2.json services/control-plane/openapi/agora_v1_2.openapi.yaml
python3 -c "import yaml; spec=yaml.safe_load(open('services/control-plane/openapi/agora_v1_2.openapi.yaml', encoding='utf-8')); paths=spec['paths']; ops=sum(1 for item in paths.values() for method in item if method in {'get','post','put','patch','delete'}); print(len(paths), ops); print('\n'.join(sorted(paths)))"
```

Observed results:

| Command | Result |
|---|---|
| `python3 scripts/test_agora_v1_2_bundle.py` | PASS, no output. |
| `pytest -q scripts/test_agora_v1_2_bundle.py` | PASS, `5 passed in 1.40s`. |
| `python3 scripts/agora_schema_bundle.py --verify` | PASS, 15 frozen v1 entries OK. |
| `python3 -m json.tool ...bundle_index.v1_2.json` | PASS. |
| `python3 -m json.tool ...capability_manifest_v1_2.json` | PASS. |
| `sha256sum ...` | PASS; values recorded below. |
| OpenAPI parse/count command | PASS, `33 35`. |

Recorded hashes:

| Artifact | SHA-256 |
|---|---|
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | `5f875202966d1e373ab325b7107de8355798f1e3f55cdac2548aa74607a821ee` |
| `services/control-plane/specs/agora/bundle_index.v1_2.json` | `7ea445d379bca1fe142272dd873c59107abe2d9cd5122d0258d40b1791b30f70` |
| `services/control-plane/specs/agora/v3/capability_manifest_v1_2.json` | `57a1edacf34d7466972b6f27c6def9516bda2a5a2c4c6894faacdb09ca5a9f7b` |
| `services/control-plane/openapi/agora_v1_2.openapi.yaml` | `12a3bf39d467e254f406ba75e4388e967b36647a49971844dcb5ae1e2611a036` |

## Recommended Reviewer Disposition

Recommended sidecar disposition: approve this packet if the reviewer agrees
that it stays support-only and the evidence above is sufficient for parent
owner consumption.

Recommended handoff message:

```text
AG-XR-OPENAPI-002-SIDECAR-REVIEW packet is ready for Codex review.
Evidence covers v1.2 hash chain, OpenAPI parse/route count, focused pytest,
frozen v1 verification, lifecycle/private-content/strategy-ref/error semantics,
and support-only boundary. Parent owner should decide whether to absorb the
packet into AG-XR-OPENAPI-002 closeout.
```
