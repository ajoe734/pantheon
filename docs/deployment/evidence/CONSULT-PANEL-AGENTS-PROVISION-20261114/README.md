# Consultation panel provisioning source acceptance

Task: `CONSULT-PANEL-AGENTS-PROVISION-20261114` · Owner: Codex · Reviewer: Claude

The Persona service now bootstraps the two panel personas on startup through its
persistent owner create/CAS path. The consultation executor provisions every
participant's exact tenant-bound `consult-*` agent before anyone is asked. The
existing admission check still independently rejects tool-bearing agents.

| Acceptance | Source proof |
| --- | --- |
| AC1: startup and replica idempotence | `services/control-plane/persona/test_panel_persona_bootstrap.py`: real lifespan, persistent JSON owner, second replica/restart no writes; bootstrap validation blocks conflicting tenants; owner CAS is unchanged. |
| AC2: authenticated ensure for all participants | `services/consultation/tests/test_panel_agent_provisioning.py`: initiating trader, risk and red team ensured through the real HTTP route before the real provider's Responses transport. |
| AC3: isolation and deny-all repair | `services/openclaw-gateway-adapter/test_consult_agent_provisioning.py`: exact IDs/workspaces, no-op replay, repair, unrelated main/trading agents preserved, bad identities/auth/tenant denied. Live admission is unchanged. |
| AC4: failures hold and retry | Integration tests cover startup bootstrap failure, Gateway ensure outage beyond the normal dead-letter threshold, and tools introduced between ensure and contribution admission. No model call or memo occurs on failure; retry succeeds after recovery. Transcript records provisioning failures. |
| AC5: non-trading boundary | The owner rejects lifecycle advancement, trading tool/route policies, class removal and trading metadata on panel records. Drafts are excluded from daily-cycle eligibility and paper admission. `consult_panel` is explicitly an advisory fleet class, excluded from quarterly ranking and hence its paper-allocation/canary recommendation inputs. |
| AC6: end-to-end source test | Missing panel personas and agents -> deployed owner lifespan -> executor -> authenticated adapter ensure -> unchanged admission -> real Responses HTTP client -> memo with three persona contributions. |
| AC7: protocol | `CONSULTATION_SESSION_PROTOCOL.md` section 9 documents bootstrap, authentication, exact agent ID, workspace/policy, status and retry behavior. |

The integration uses real persona, consultation, registry, governance handoff and
memory HTTP handlers. Owner storage uses JSON/in-memory fixtures instead of
PostgreSQL. Memory authorization uses the existing local governance policy. Only
OpenClaw transport/model output is faked: the CLI RPC runner forwards to the fake
Gateway HTTP server, and the actual provider uses that server's Responses SSE
endpoint. The inherited support fixture's owner-shaped fact endpoints are not
used by this acceptance; real Registry and Persona URLs replace them.

Additional necessary contact points beyond the initial file list are
`services/persona/write_owner.py` (enforce the non-trading owner invariant),
`services/control-plane/bff/personas/fleet_classification.py` (exclude panel
personas from trading rankings), `docker-compose.yml` and
`docker-compose.control.yml` (tenant and authenticated owner-read wiring), and
two existing consultation test fixtures (acknowledge the new ensure contract).
Real Registry integration also exercises the executor's `owner_tenant` field
and nullable lineage list handling.

No hosted deployment or real model quality is claimed. Hosted proof remains
`PERSONA-CYCLE-RECONNECT-HOSTED-ACCEPTANCE-20261112` AC3. Review and merge identity
are recorded by canonical handoff/approval and GitHub, rather than self-attested
in this file. See `evidence.json` for terminal local-validation results.
