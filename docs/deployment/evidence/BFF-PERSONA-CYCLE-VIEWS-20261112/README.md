# BFF persona cycle read contract

Task: BFF-PERSONA-CYCLE-VIEWS-20261112. Owner: Codex2. Reviewer: Claude2.
Source-only implementation evidence; independent review is pending.

Both existing detail entrypoints (`GET /bff/personas/{id}` and
`GET /api/v1/operator/persona-management/{id}`) return `data.personaCycle`.
The existing `PersonaService` binds the owner reads after the routes resolve
and authorize the tenant-scoped persona. The existing strategyPool contract
is preserved. No new route, credential, owner store or mutation is added.

| Field | Owner and contract |
| --- | --- |
| `cycle.health` | Controller's `cycle_health` projection of the newest paper packet, ordered by cron run time then update time; preserves stage, failure reason, harness version and deterministic risk result. |
| `cycle.packets` | Latest 20 scoped packet summaries. Prompts, case files, staged memory and agent output are excluded. |
| `memorySummary` | Memory `/api/memory/retrieve`, authorized by Governance; at most 100 owner-ranked entries with identity, type, timestamp and summary. `returned` is the returned count, not a total inventory count. |
| `harnessExperiences` | Separate Memory retrieval tagged `harness_experience`, at most 100 owner-ranked entries; projects the persisted outcome, cue, lesson, confidence, evidence and version. Pending Persona outbox delivery is not presented as Memory truth. |
| `consultationMemos` | Consultation service's memos from the latest 20 persona panels initiated by this persona; memo/request identity is checked before projection. Other personas' panels and transcripts are excluded. |
| `harness` | Persona `/api/personas/{id}/harness`; owner `HarnessVersion` schema validates every version and its persona identity. Returns active version and version metadata; prompt/tool/workflow content is excluded. |

Each field has an independent `meta.surfaces` entry: `persona_cycles`,
`persona_memory_summary`, `harness_experiences`, `persona_consultation_memos`,
`persona_harness`. Missing, empty, unauthorized, malformed or unreachable owner
data yields null with `status=unavailable` and a safe reason. Other fields remain
available. The envelope's existing persona metadata remains unchanged; consumers
must inspect the corresponding field's surface status.

`GET /bff/management/persona-fleet` adds `persona_cycle_health` to each authorized,
paginated row. It reads the controller owner once for the page. Missing owner
records yield `status=unavailable` and a reason. This overlay adds no canonical
loop row and makes no claim about cron health between recorded runs.

Controller reads use the injected OODA owner or its existing shared JSON/JSONL
decoder and configured store path. Reads never create or rewrite that file.
Memory and Persona receive the caller's existing credential and tenant;
Consultation uses its existing configured service credential with its tenant
header. Governance continues to authorize Memory retrieval. Memory GET retrieval
may update its owner's existing reuse bookkeeping; the BFF issues no writes.

Design authority: D13 and sections 2.1–2.5 of
`docs/04/pantheon_persona_cycle_reconnect_2026-11-12/SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md`.
Original sources read: `Pantheon_總索引版系統分析文件.md` §6.4,
`PERSONA_RUNTIME_MODEL.md` §6, `services/memory/MEMORY_LAYER_DESIGN_NOTE.md`
§3/§4, `docs/03/SD-02_persona_governance.md` §4.8,
`docs/05/system-verification-rounds/INDEX.md` composite overlay boundary.

Tests bridge urllib transport to the real Persona, Consultation, Memory and
Governance FastAPI applications and actual owner stores. The real controller
packet encoder/decoder and BFF application service/router run. Test-only owner
records and signed test credentials do not constitute hosted or model evidence.
See evidence.json for exact commands, results and baseline limitations.
