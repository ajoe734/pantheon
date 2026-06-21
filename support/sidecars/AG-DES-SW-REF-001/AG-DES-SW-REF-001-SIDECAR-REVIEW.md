# AG-DES-SW-REF-001 Review Packet and Evidence Summary

**Sidecar Task ID**: `AG-DES-SW-REF-001-SIDECAR-REVIEW`
**Parent Task**: `AG-DES-SW-REF-001`
**Sidecar Owner**: `Claude`
**Sidecar Reviewer**: `Claude2`
**Helper Kind**: `review_packet`
**Generated**: 2026-06-21
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime/registry/governance implementation, BFF implementation,
or any other canonical design surface. The parent owner decides whether and how
to absorb this packet into the main execution slice.

This sidecar did not inspect `current-work.md` or the full `ai-activity-log.jsonl`;
it used the task brief, `ai-status.json`, the deep-closure document, the existing
Registry contract, the v1.1 Agora contract closure files, and the current
`strategy_workshop.schema.json`.

---

## 1. Parent Task Purpose

`AG-DES-SW-REF-001` must produce the **Strategy Registry reference and
workshop-version mapping contract** — one of four design artifacts required before
`AG-BE-SW-001` (Strategy Workshop backend implementation) can be dispatched.

Source of record for this requirement:

```
docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/
  AG-BE-SW-001_deep_design_closure_2026-06-21.md
  §11 — Required contract artifacts before execution resumes
```

The other three blocked siblings are:

| Task | Contract scope |
|---|---|
| `AG-DES-SW-PRIV-001` | Private-content storage, encryption, retention, redaction |
| `AG-DES-SW-DB-001` | Workshop tables, lifecycle alignment, exact index migration |
| `AG-XR-OPENAPI-002` | Additive Agora v1.2 OpenAPI/capability/schema bundle |

`AG-BE-SW-001` depends on all four. Until all four are merged and hashed,
`AG-BE-SW-001` must remain `blocked_design / dispatchable: false`.

---

## 2. Contract Scope Required by AG-DES-SW-REF-001

The following scope is mandated by the deep-closure document (§5).
This packet does not rewrite or extend that mandate; it organises the
required contract elements so the reviewer can validate coverage.

### 2.1 Canonical identity vocabulary

Three identifiers must be precisely distinguished in the contract:

| Identifier | Meaning | Owner |
|---|---|---|
| `strategy_id` | Stable strategy family identity across all versions | Strategy Registry |
| `strategy_spec_registry_id` | Immutable Registry record or version ID (one per registered version) | Strategy Registry |
| `active_strategy_spec_registry_id` | Workshop-level pointer to the currently selected Registry version | Workshop session table |

The contract must explicitly state that workshop tables store **references only**;
they must never copy StrategySpec JSON.

### 2.2 Create-from-existing-draft path

When a workshop creation request supplies `strategy_ref`:

1. BFF resolves the Strategy Registry record by `strategy_spec_registry_id`.
2. BFF verifies tenant/user scope.
3. If a `strategy_id` is also supplied, BFF checks `record.strategy_id` matches.
4. BFF stores only `strategy_id` and `active_strategy_spec_registry_id` on the
   workshop session.
5. StrategySpec JSON is **not** copied into workshop tables.

Error contract:
- Mismatched `strategy_id` vs. registry record → `409 STRATEGY_REFERENCE_MISMATCH`
- Missing or unauthorised record → `404` or `403` without existence leakage

### 2.3 Create-from-free-form path

When no `strategy_ref` is supplied at workshop creation:

```text
strategy_id                      = NULL
active_strategy_spec_registry_id = NULL
```

When the first user-accepted strategy version is subsequently created:

1. BFF calls the existing Strategy Registry draft-create path.
2. Registry returns `strategy_id` and an immutable `strategy_spec_registry_id`.
3. BFF inserts a `strategy_workshop_version_link` row.
4. BFF updates `strategy_id` and `active_strategy_spec_registry_id` on the
   workshop session in the same orchestrated command.

### 2.4 Workshop version link semantics

A workshop version is a **link to an immutable Strategy Registry version**,
not a copied StrategySpec document.

Required fields for `strategy_workshop_version_link`:

| Field | Type | Notes |
|---|---|---|
| `workshop_version_id` | text PK | |
| `workshop_id` | text FK | → workshop session |
| `strategy_id` | text NOT NULL | |
| `strategy_spec_registry_id` | text NOT NULL | |
| `parent_workshop_version_id` | text NULL | |
| `source_event_id` | text NULL | |
| `sequence_no` | bigint NOT NULL | |
| `created_by` | text NOT NULL | |
| `created_at` | timestamptz NOT NULL | |

Required constraints:

```sql
UNIQUE (workshop_id, sequence_no)
UNIQUE (workshop_id, strategy_spec_registry_id)
```

These constraints prevent duplicate Registry-version links per workshop and
enforce monotonic version ordering.

### 2.5 Deprecated and aliased fields

The contract must declare disposition for three ambiguous fields currently
present in the v1.1 Agora contract:

| Field | Disposition |
|---|---|
| `strategy_spec_ref` (v1.1 request body) | Deprecated alias for `strategy_spec_registry_id`; accept but map, do not propagate |
| `selected_version_id` (v1.1 response) | Response alias for the active `workshop_version_id`; must not create a second StrategySpec truth column |
| `active_strategy_spec_registry_id` | Authoritative Registry pointer stored on the session; canonical going forward |

### 2.6 Conclude semantics

`POST /bff/agora/workshops/{id}/conclude` requires an existing
`strategy_workshop_version_link` row.

On conclude the session records:

```text
final_workshop_version_id       → the workshop_version_id of the final link
final_strategy_spec_registry_id → the corresponding strategy_spec_registry_id
concluded_at                    → timestamp
```

Conclude must **not** promote the StrategySpec lifecycle state in the Strategy
Registry. Registry/governance transitions (`draft → candidate → approved`) remain
the exclusive responsibility of the governance/registry service.

---

## 3. Current State Assessment

### 3.1 Existing artifacts relevant to this contract

| Artifact | Relevance | Gap |
|---|---|---|
| `services/registry/contract.md` | Canonical Strategy Registry contract (REG-001/REG-004). Defines `strategy_id`, `strategy_spec_registry_id` (as `registry_id`), artifact-state model. | Does not define workshop-to-registry reference semantics, workshop-version links, or `active_strategy_spec_registry_id` at the workshop layer. |
| `services/control-plane/specs/agora/strategy_workshop.schema.json` | Existing workshop schema (v1.0). Uses `subject.ref` (free-form string) for strategy reference. | Lacks separate `strategy_id` / `strategy_spec_registry_id` fields; no `active_strategy_spec_registry_id`; no `workshop_version_link` concept. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | v1.1 contract closure for AG-BE-SW-001 routes and schema. Names `selected_version_id` and `active_strategy_spec_registry_id` but does not specify their exact semantics or the version-link table. | `selected_version_id` vs `active_workshop_version_id` disambiguation is incomplete. Version-link table definition is absent. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md` | Authoritative deep-closure mandate (§5). Fully specifies the required contract scope. | This is the source of truth for what AG-DES-SW-REF-001 must produce; it is not yet materialised as a standalone contract artifact. |

### 3.2 Identified gaps the contract must close

| Gap | Current state | Required resolution |
|---|---|---|
| Workshop schema uses `subject.ref` (string) | `strategy_workshop.schema.json` §subject.ref | Replace with explicit `strategy_id` + `active_strategy_spec_registry_id` fields in the v1.2 schema |
| No `strategy_workshop_version_link` table definition | Absent from all existing contract files | AG-DES-SW-REF-001 or AG-DES-SW-DB-001 must define the table; AG-DES-SW-REF-001 owns the semantic contract, AG-DES-SW-DB-001 owns the executable migration |
| `strategy_spec_ref` deprecation not formalised | v1.1 contract names it; disposition unclear | AG-DES-SW-REF-001 must formally deprecate and specify backward-mapping |
| Conclude semantics incomplete | Contract closure doc describes route; version-link requirement and registry non-promotion rule are not in a standalone contract | AG-DES-SW-REF-001 must make these explicit |
| Free-form → Registry creation path unspecified | Not present in any existing contract | AG-DES-SW-REF-001 must specify the two-step path (Registry draft-create → link insert → pointer update) |

---

## 4. Evidence Inspected

| Artifact | Path |
|---|---|
| Deep-closure mandate (authoritative) | `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md` |
| Registry canonical contract | `services/registry/contract.md` |
| v1.1 Workshop route/contract closure | `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` |
| Existing workshop JSON schema | `services/control-plane/specs/agora/strategy_workshop.schema.json` |
| Existing bundle index (v1 and v1.1) | `services/control-plane/specs/agora/bundle_index.json`, `bundle_index.v1_1.json` |
| OpenAPI bundles (v1 and v1.1) | `services/control-plane/openapi/agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml` |
| Active task state | `ai-status.json` |

No runtime tests were run. Focused test verification is not required for this
design-contract sidecar; it applies only to implementation tasks.

---

## 5. Non-Claims

This review packet does not claim:

| Non-claim | Correct disposition |
|---|---|
| That AG-DES-SW-REF-001 is complete or merged | The parent task artifact does not yet exist; this packet summarises what it must contain |
| That this sidecar writes any contract | Only the parent owner may create or merge `AG-DES-SW-REF-001` canonical contract files |
| That `AG-BE-SW-001` is unblocked | It remains blocked pending all four design artifacts merged and hashed |
| That workshop-version links have been implemented | No implementation exists yet; the schema and semantic contract are the prerequisite |
| That StrategySpec lifecycle management is in scope here | Registry/governance own that gate; this contract only defines the reference and link semantics |

---

## 6. Dependency Map

```
AG-DES-SW-REF-001  (this contract)
  └─ defines: strategy_id, strategy_spec_registry_id, active_strategy_spec_registry_id
              workshop_version_link semantics
              conclude semantics
              deprecated-field disposition
  └─ consumed by:
       AG-DES-SW-DB-001    (must produce executable strategy_workshop_version_link migration)
       AG-XR-OPENAPI-002   (must add v1.2 schema fields referencing these identifiers)
       AG-BE-SW-001        (BFF implementation — reads Registry via strategy_spec_registry_id,
                            stores only references, enforces conclude pre-condition)

AG-DES-SW-REF-001  does NOT consume or modify:
  - services/registry/contract.md           (read-only dependency)
  - services/control-plane/specs/agora/*    (v1 and v1.1 bundles are immutable)
  - services/control-plane/openapi/agora_v1*.openapi.yaml (immutable)
```

---

## 7. Reviewer Checklist for Claude2

| Check | Expected answer |
|---|---|
| Does this packet accurately scope AG-DES-SW-REF-001 from the deep-closure mandate? | Yes — §2 maps directly to deep-closure §5 without extension |
| Does §2.1 distinguish all three identifiers without ambiguity? | Verify: `strategy_id`, `strategy_spec_registry_id`, `active_strategy_spec_registry_id` each have a single clear definition |
| Does §2.2 correctly capture the create-from-existing-draft guard conditions? | Verify: tenant/user scope, strategy_id mismatch → 409, missing record → 404/403 without existence leakage |
| Does §2.4 specify the version-link table fields and uniqueness constraints completely? | Verify against deep-closure doc §5.4 |
| Does §2.6 correctly prohibit Registry lifecycle promotion at conclude time? | Yes — conclude only records final pointers; Registry/governance retain artifact_state authority |
| Does §3.2 accurately identify the gaps against the current codebase? | Verify by reading `strategy_workshop.schema.json` and confirming no `strategy_spec_registry_id` field exists there |
| Does this sidecar avoid canonical/implementation edits? | Yes — only this support packet file is created |
| Are the four sibling design tasks correctly listed as co-requirements? | Verify against deep-closure §11 |

---

## 8. Handoff

**To**: `Claude2`
**From**: `Claude`
**Requested review outcome**: Approve this sidecar if the review packet accurately
captures the scope and gaps that `AG-DES-SW-REF-001` must close, and the
dependency map correctly identifies what downstream tasks consume.

Recommended reviewer disposition:

1. Approve if §2 (contract scope) and §3 (gap assessment) faithfully represent
   the deep-closure mandate without introducing new scope or misrepresenting
   the current codebase state.
2. Request changes if any required scope element from deep-closure §5 is missing
   or if the gap assessment mischaracterises an existing artifact.
3. Do not treat this packet as authority to initiate AG-BE-SW-001 execution;
   that gate remains with the parent owner and all four design artifact merges.
