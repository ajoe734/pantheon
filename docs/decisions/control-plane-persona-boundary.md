# Control Plane Persona Boundary

Status: draft-canonical
Last updated: 2026-04-22
Source of truth inputs:
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Followup_Questions.md`
Tier: L1 Platform Architecture & Policy
Scope: persona-service canonical ownership versus BFF-composed persona views
Conflict rule: this decision governs persona boundary semantics until superseded by a newer explicit persona-boundary decision

## Decision

The persona service owns canonical persona object truth. The BFF owns only
operator-facing aggregation and composed presentation.

## Persona-owned canonical truth

At minimum, persona-owned truth includes:

- `Persona`
- `PersonaLifecycle`
- `PersonaMandate`
- `RoutePolicyRef`
- `ConsultPolicyRef`
- `PersonaCapabilityProfile`
- `PersonaSession` metadata
- `PersonaToolProfileRef`
- `PersonaRiskPosture` metadata
- persona eligibility metadata that belongs to persona policy, not capital
  binding authority

## Not persona-owned canonical truth

The persona service does not own canonical truth for:

- `DeploymentPlan`
- `RuntimeBinding`
- `ApprovalDecision`
- `CapitalPool`
- `TelemetryEvent`
- `IncidentCase`
- `EvolutionDecision`

## BFF-owned composed read truth

The BFF may compose:

- deployment rollups
- incident rollups
- review summaries
- badges / chips / convenience metadata
- latest operator warnings
- cross-domain stitched operator views
- action affordances from backend-provided `allowedActions`

The BFF must not replace persona-owned canonical truth with a shadow persona
object.

## Special note: PersonaCapitalBinding

`PersonaCapitalBinding` is not owned solely by persona service.

It is a capital / governance boundary object.

Persona service may read and display eligibility for a binding, but must not
become the write truth for the binding itself.

## Consequences

1. Persona identity, lifecycle, policy refs, mandate, and session metadata
   should not remain only in a BFF-composed layer.
2. Stub or deferred persona service work should be promoted toward canonical
   object ownership, not indefinitely papered over in the BFF.
3. Operator-facing convenience views may stay composed in the BFF as long as
   they do not displace upstream persona truth.
