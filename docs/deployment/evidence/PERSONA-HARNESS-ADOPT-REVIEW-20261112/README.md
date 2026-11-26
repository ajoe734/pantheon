# Paper harness adoption and review

Task: `PERSONA-HARNESS-ADOPT-REVIEW-20261112`  
Owner: Codex; assigned independent reviewer: Claude.  
Design: reconnect design §6.8 / D10. This is source implementation evidence;
no hosted adoption, efficacy, deployment or live-capital claim is made.

The Persona owner now connects its existing `activate` operation to the stored
shadow result and published, exact-candidate consultation evidence. It reruns
the precheck, rederives required risk/red-team participants, and evaluates their
independent transcript recommendations. A synthesis claiming approval cannot
override a risk contribution. Outside paper, proposals are retained and sent to
Evolution for operator governance; the harness owner does not activate them.

The observation owner operation is
`POST /api/personas/{persona_id}/harness/patches/{patch_id}/observe`, with
`{"packet_ids": ["<owner-packet-ref>"]}`. It uses the same existing Persona-plane
writer authorization and exact tenant boundary as other harness operations.
The request contains references only. `ObservationFacts` in
`services/persona/harness_adoption.py` defines the required frozen owner data at
`persona_cycle.observe_bundle.harness_observation`:

| Field | Owner fact |
| --- | --- |
| `patch_id`, `base_version`, `candidate_version` | Exact experiment and arms |
| `trading_date` | Actual trading date supplied by the owner; no weekday/calendar inference |
| `criterion` | Adopted `net_of_costs_total_return`, higher is better |
| `base_utility`, `candidate_utility` | Paired measured daily values, nullable when unmeasurable |
| `confounders` | Explicit recorded conditions preventing attribution |
| `evidence_refs`, `confidence` | Real book-window evidence and confidence |

The surrounding packet must be terminal, paper, same tenant/persona and adopted
harness version, without unavailable Observe inputs. Review requires the first
ten recorded distinct trading dates after adoption, rather than a caller-chosen
favourable subset. Missing/invalid facts record reasons and retain the active
experiment. A complete window with unmeasurable paired values or confounders
records `confounded`. Otherwise the sign of summed paired utility differences
determines `helped`, `hurt`, or `no_effect`.

The existing Memory Learn-feedback API receives a stable `harness_experience`
source event (PersonaMemory `strategy_lesson`, InstitutionalMemory
`cross_persona_observation`). The body retains the paired facts and delta;
cue, lesson, evidence and confidence survive writeback. Component-relevant
experiences are automatically attached to subsequent patch proposals.
`hurt` also sends a medium-risk governance proposal containing the rollback
target and experience; it does not execute a rollback. An observed current
version remains eligible for the existing explicit owner rollback operation.

Only one experiment may be active. The default seven-calendar-day component
cooldown begins on review or rollback and survives restart. Surface keys define
components, so patch-type aliases cannot avoid cooldown. Policy docs were
amended in `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §9.3.1 and the Memory design
note §4.1.2. The DSPy prompt-bundle operator gate remains in force.

Delivery intents and errors remain in the Persona aggregate's `outbox`, visible
through the existing harness read API. Network calls occur outside owner CAS;
receipts must match stable identities. Existing Memory tokens (including file
credentials) and Evolution auth are used. Observation retries and
`HarnessAdoption.deliver(persona_id, actor)` replay pending deliveries, at most
four intents per call. Evolution replay resolves an existing decision before
POST; an unrelated receipt cannot be reused. Pending delivery is not a claim
that Memory or governance accepted the event.

The source tests exercise owner routes, the actual shadow evaluator, durable
packet and persona stores, actual Consultation panel execution, strict-auth
Memory HTTP writeback and authenticated Evolution proposal HTTP. Provider
answers and paired book measurements are fixtures confined to tests. The
preexisting append-only-version test now verifies cooldown rejection before
advancing its test clock.

Operational limits for the independent reviewer:

- This task consumes paired owner book facts. It does not create trading dates,
  a counterfactual book producer, or synthetic observations when that feed is
  absent. The current daily-cycle collector does not manufacture this extension;
  a deployment without such recorded facts refuses observation completion.
- The inherited shadow evaluator still requires its dedicated deny-all replay
  provider and live isolation probe; this task does not configure that provider.
- Observation and pending-delivery recovery are owner operations. No second
  scheduler or new deployment configuration is introduced.
- The paper amendment does not grant risk, capital, lifecycle, live or order
  authority. Governance proposal delivery is not operator approval or execution.

Exact validation commands and terminal results are recorded in `evidence.json`.
