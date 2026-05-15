# Pantheon Response to Architecture Team Design Input List

## Purpose
This document is the formal response to the implementation team's
`2026-04-20-architecture-team-design-input-list.md`.

It is not intended to redraw the entire Pantheon high-level blueprint.
Its purpose is to:

1. confirm which judgments we agree with,
2. confirm which items should remain in the architecture bucket,
3. confirm which items should not be sent back to architecture,
4. add the global rules and deliverables we believe still need to be defined,
5. provide a clear basis for implementation, BFF work, frontend work, and Lovable handoff.

## 2026-04-20 Update After System Design Response
This document has now been re-read against
`docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`.

As a result, the following points are now treated as integrated blueprint truth:

- `RW-05` is no longer treated as a missing-contract module; it is `contract_ready`
  and may move into implementation.
- `KW-02` / `KW-03` / `KW-04` are treated as `contract_ready` with pending-BFF
  readiness, not as fully implemented and not as shell-only.
- `KW-05` remains blocked.
- `CW-03` is allowed to partial-activate before `CW-02` is fully live, but it
  must not be promoted as full module-ready yet.

I also retain four integration objections that must be resolved in the
canonical conventions layer:

1. `allowedActions` should remain object-shaped, not an array.
2. The shared response envelope must not require every detail payload to expose
   generic `id` / `title`.
3. Current repo truth uses `page_info.next_page_token`, not `next_cursor`.
4. The degradation and readiness vocabularies still require a formal crosswalk.

---

# 1. Overall conclusion

After reviewing the design input list in full, my conclusion is:

## 1.1 I agree with the main judgment
What truly needs to be added now is not a new high-level Pantheon blueprint, but:

- global canonical conventions,
- a small number of cross-service ownership decisions,
- a small number of module-level canonical contracts that are still not locked,
- a small number of modules whose ready / not-ready status has drifted and now requires architecture ratification.

I agree with the document’s core judgment:

> Most blocked modules are not missing high-level blueprint work and are not primarily blocked on frontend work; they are blocked because they still lack BFF-facing canonical module contracts.

So this document is not asking us to redraw Pantheon. It is asking us to move certain modules from “high-level intent exists” to “implementation handoff ready.”

## 1.2 I agree with the lane split
I agree with separating work into:

1. **Architecture Bucket**
   - global conventions
   - ownership decisions
   - module-level canonical contract
   - architecture ratification

2. **Non-Architecture Bucket**
   - route already live
   - contract already published
   - remaining work is mainly implementation / truth-hardening / UI activation / wiring

This split is reasonable.

---

# 2. What I agree with

## 2.1 A1 Global Canonical Conventions Pack must be completed first
The document asks for:

- `module-level canonical contract != new deployable service`
- shared response envelope
- global `allowedActions` rule
- `meta.snapshot_at`
- `meta.surfaces.*`
- lifecycle / state naming
- list route pagination / cursor / ordering / filter naming
- module readiness ladder

And it expects at minimum:
- global contract conventions
- degradation dictionary
- readiness classification documents

### My response
I fully agree, and I believe this should be the top priority.

If this layer is not standardized first, then even if each module is later completed one by one, we will still end up with:

- different pagination approaches across list routes,
- inconsistent detail response envelopes,
- different `allowedActions` naming patterns,
- inconsistent semantics for `degraded / stale / partial / unavailable`,
- and drifting readiness definitions.

At that point, the frontend will still not be able to build a coherent workbench.

---

## 2.2 B1 Lineage Ownership must be decided first
The document points out that lineage truth currently appears across multiple paths, and therefore asks to decide:

- who the canonical lineage read owner is,
- which path the BFF should connect to,
- whether `lineage-read` remains, wraps the telemetry engine, or is eventually absorbed by the telemetry path.

### My response
I agree this belongs in the architecture bucket, not something the implementation team should choose ad hoc.

### My proposed decision
- canonical write truth for normalized lineage edges should remain with the respective domain owners,
- UI-facing lineage read truth should be unified behind `lineage-read`,
- the telemetry lineage engine may exist as an implementation substrate or internal dependency,
- but the BFF should only consume `lineage-read`, and must not also consume a separate telemetry lineage projection.

### Summary
**`lineage-read` should be the UI-facing canonical read owner; the telemetry lineage engine may exist internally, but it should not become a second UI truth path.**

---

## 2.3 B2 Persona Boundary must be clarified first
The document notes that the persona service still has stub / deferred portions and therefore we must first define:

- what belongs to persona service canonical truth,
- what belongs only to a BFF-composed read model.

### My response
I agree.

### My proposed decision
#### Persona service canonical truth should include at least:
- `Persona`
- `PersonaLifecycle`
- `RoutePolicyRef`
- `ConsultPolicyRef`
- `PersonaCapabilityProfile`
- `PersonaCapitalEligibility`
- `PersonaSession` (at least at the metadata level)

#### BFF-composed read models should include:
- latest deployment / incident / review rollups
- display badges / chips
- operator convenience summaries
- cross-domain read composition

### Summary
**The persona service should own the canonical persona object; the BFF should only own operator-facing aggregation.**

---

## 2.4 B3 Router Enforcement Ownership must be clarified first
The document notes that the router still defers some enforcement to the gateway, and approval workflow still has surrogate/stub behavior. It therefore asks to define:

- TTL enforcement owner
- rate-limit enforcement owner
- whether approval / routing authority belongs to router or gateway / another control surface
- whether local intent classifier keeps any production fallback role

### My response
I agree this is an architecture bucket item.

### My proposed decision
- **router** owns request routing decision, intent capture, and route selection
- **gateway / BFF edge** owns ingress concerns only (authentication, transport, basic request shaping)
- **approval authority** belongs neither to gateway nor router; it belongs in governance / promotion control surfaces
- **TTL / rate-limit**
  - edge/gateway may perform transport-level throttling
  - domain-level TTL / command validity must be explicitly enforced by router or downstream owner
- **local intent classifier**
  - may remain as degraded fallback
  - must not become a production canonical truth source

### Summary
**Gateway must not become a business authority; router can decide traffic flow, but it must not replace governance authority.**

---

## 2.5 Category C modules do still lack module-level canonical contracts
I agree that the following should remain in architecture completion rather than be pushed directly to implementation:

- CW-02 Debate Transcript
- CW-04 Red-team Memo
- TW-02 Parameter Controls
- KW-05 Strategy Spec

### My module-by-module position

#### CW-02 Debate Transcript
Agreed.
Append-only `TranscriptEvent` schema, ordering, actor labeling, and evidence-link semantics must be defined first.

#### CW-04 Red-team Memo
Agreed.
This module touches governance handoff, so implementation should not invent its own action semantics.

#### TW-02 Parameter Controls
Agreed.
This directly affects preview / replay / commit / discard / validation boundaries.

#### KW-05 Strategy Spec
Agreed.
This is versioned spec truth and must be formalized first.

#### RW-05 Artifact Compare
Update after the system-design response:
`RW-05` should no longer sit in this bucket. Its contract is now treated as
`contract_ready`; the remaining work is BFF implementation and backlog /
overview truth alignment.

---

## 2.6 D1 Ratification-type items are correctly categorized
The document notes that KW-02 / KW-03 / KW-04 suffer primarily from:

- some docs / screens / lovable packets already existing,
- while some overview or backlog sources still mark them as not ready,
- meaning the missing step is architecture ratification, not redesign.

### My response
I agree.
This is a readiness-classification problem, not a code problem.

### My proposed action
Architecture should produce:

`MODULE_READINESS_RATIFICATION_2026-04-20.md`

At minimum listing:
- module
- existing docs
- canonical status
- implementation allowed? yes / no
- frontend handoff allowed? yes / no
- if no, which missing artifact is blocking it

Integrated result after the formal response:

- `KW-02` / `KW-03` / `KW-04` are ratified as `contract_ready` with pending-BFF
  readiness.
- `RW-05` is ratified as `contract_ready`; it is no longer a missing-contract
  item.
- `KW-05` remains blocked.
- `CW-03` requires a packet-promotion rule for partial activation vs full
  module-ready.

---

## 2.7 The non-Architecture Bucket classification is broadly correct
I agree that the following should no longer be stuck in architecture lane and should move to implementation / truth-hardening / UI activation / wiring:

- EW-04
- RW-02
- RW-04
- RW-05
- CW-01
- TW-01
- TW-03
- TW-04
- RW-01
- RW-03
- CW-03
- KW-01
- EW-05

If they already have contract-published or route-live status, they should not keep cycling back to architecture.

---

# 3. What I believe still needs strengthening

The following are not objections; they are additions I recommend for the final convergence package.

## 3.1 Elevate “module-level contract != new deployable service” into a front-page principle
The current document already mentions this, but I recommend moving it from the conventions pack into the opening principles section.

### Recommended wording
> The purpose of adding module-level canonical contracts is to provide a source of truth for BFF, packets, UI, and implementation.
> **It is not a requirement that every module become a new deployable service.**

---

## 3.2 Add a minimal fixed-field shared response envelope
The document mentions response envelope conventions but does not define the minimum fixed fields.

### I recommend standardizing that detail / list responses should include at least:
- a domain-specific primary identifier (`decision_id`, `request_id`, `session_id`,
  `artifact_id`, etc.), not necessarily a forced generic `id`
- `title` or `display_name` where the surface naturally owns one
- `status`
- `lifecycle_state` (where applicable)
- object-shaped `allowedActions`
- `meta.snapshot_at`
- `meta.surfaces.<surface_name>`
- `links` (where applicable)
- and for list routes additionally:
  - `items`
  - `page_info.next_page_token`

---

## 3.3 Elevate `allowedActions` into a hard global rule
I recommend formally stating in conventions:

> **Frontend must not derive CTA availability from actor role + object state.**
> All executable actions must come from backend-provided `allowedActions`.

This is already implied across multiple modules but should become a global rule.

---

## 3.4 `meta.surfaces.*` needs a global degradation dictionary
I recommend that architecture formally provide:

`DEGRADATION_DICTIONARY.md`

At minimum standardizing:
- `ok`
- `stale`
- `degraded`
- `unavailable`

But this dictionary still needs one explicit crosswalk:

- when `fresh/stale` are legal per-surface statuses,
- when surfaces must use `ok/degraded/unavailable`,
- whether `partial` remains a valid surface enum,
- and whether some stale states are derived from `meta.staleness` rather than
  encoded directly as a surface status.

---

## 3.5 Module readiness ladder needs a formal enum
I recommend the readiness ladder be formally defined as:

- `blocked`
- `contract_ready`
- `screen_ready`
- `handoff_ready`
- `implementation_ready`
- `production_ui_ready`

And that all of the following use exactly this same readiness enum:
- backlog
- BFF overview
- lovable packets
- screens docs

This also needs an explicit mapping from current repo vocabulary such as
`contract-published`, `pending-bff`, `route-live`, `ready`, and `shell-only`.
Without that mapping, the new ladder will add another layer of drift rather than
remove it.

---

# 4. Items I agree should remain in the architecture bucket

## 4.1 Global rules
- Global Canonical Conventions Pack

## 4.2 Ownership decisions
- LIN-002 Lineage Ownership
- Control Plane Persona Boundary
- Control Plane Router Enforcement Ownership

## 4.3 Modules still lacking canonical contracts
- CW-02 Debate Transcript
- CW-04 Red-team Memo
- TW-02 Parameter Controls
- KW-05 Strategy Spec

## 4.4 Architecture ratification
- KW-02 / KW-03 / KW-04 readiness ratification
- CW-03 partial-activation promotion rule

---

# 5. Items I agree should no longer be sent back to architecture

The following should move to implementation / truth-hardening / UI activation:

- EW-04
- RW-02
- RW-04
- RW-05
- CW-01
- TW-01
- TW-03
- TW-04
- RW-01
- RW-03
- CW-03
- KW-01
- EW-05

---

# 6. Deliverables I believe architecture should produce in this round

## 6.1 Global deliverables
1. `docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md`
2. `docs/conventions/DEGRADATION_DICTIONARY.md`
3. `docs/conventions/MODULE_READINESS_LADDER.md`
4. `docs/conventions/BFF_RESPONSE_ENVELOPE.md`

## 6.2 Per-module deliverables
1. `docs/bff/<module>.md`
2. `docs/screens/<module>.md` (where the module is already screen-ready)
3. `docs/examples/<module>.json`
4. readiness update
5. `WORKBENCH_DELIVERY_BACKLOG.md` row update
6. where write actions exist:
   - command vocabulary
   - module-local write route contract
7. where lifecycle exists:
   - explicit state machine

## 6.3 Ratification deliverables
1. `MODULE_READINESS_RATIFICATION_2026-04-20.md`
2. explicit canonical status for RW-05, KW-02 / KW-03 / KW-04, and KW-05
3. explicit promotion rule for `CW-03` partial activation vs full module-ready
4. if any remain not-ready, state exactly which artifact is missing rather than only labeling them not_ready

---

# 7. Final conclusion

I support this design input list as the next architecture completion checklist.
It does not need to redraw Pantheon. It needs to:

- complete global conventions,
- finalize a small number of ownership decisions,
- complete a small number of module-level canonical contracts,
- and ratify readiness status where documentation and backlog have drifted apart.

At the same time, I request 5 additions:

1. elevate `module-level contract != new deployable service` into a front-page principle
2. explicitly define the minimal shared response envelope
3. elevate `allowedActions` into a global hard rule
4. provide a full global degradation dictionary
5. provide a formal readiness ladder enum and ratification output

### One-sentence summary
> I agree with the main conclusion and lane split of this design input list.
> What truly needs to be sent back to architecture now is not the whole Pantheon high-level blueprint, but global canonical conventions, a small number of cross-service ownership decisions, a small number of modules whose canonical contracts are still not locked, and a small number of readiness-ratification issues. Contract-published or route-live items should no longer be blocked in architecture lane.
