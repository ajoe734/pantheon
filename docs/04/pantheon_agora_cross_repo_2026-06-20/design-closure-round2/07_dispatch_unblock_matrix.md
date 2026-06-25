# Dispatch Unblock Matrix — Round 2

## New design/contract tasks

| Task | Deliverable | Owner lane | Reviewer |
|---|---|---|---|
| `AG-DES-VERS-001` | patch/version compare/readiness prose + schemas | system design | Codex |
| `AG-DES-RS-001` | research facade/stage routing/run projection | system design | Claude |
| `AG-DES-SSE-001` | typed workshop SSE contract | system design | Codex |
| `AG-DES-TR-001` | Trading Room aggregate/intent handoff | system design | Claude |
| `AG-DES-CARD-001` | workshop card projection contracts | system design | frontend reviewer |
| `AG-DES-E2E-001` | winner-branch and isolation acceptance | system design | Codex |
| `AG-XR-OPENAPI-004` | additive v1.3 OpenAPI/capability/schema bundle + generated hashes | integration/schema | Claude |

## Downstream unblock conditions

| Downstream task | Remains blocked until |
|---|---|
| `AG-BE-SW-002` | VERS schema/routes merged and generated types available |
| `AG-FE-SW-003` | VERS + CARD contracts mirrored to frontend |
| `AG-BE-RS-004` | VERS + RS merged |
| `AG-FE-RS-001` | VERS + RS + CARD generated types mirrored |
| `AG-BE-RS-001` | RS facade/OpenAPI merged |
| `AG-BE-RS-002` | RS routing/projection schema merged |
| `AG-BE-SW-004` | SSE event schema/OpenAPI merged |
| `AG-BE-TR-001` | TR aggregate contract merged |
| `AG-BE-TR-002` | governed intent/handoff contract merged |
| `AG-FE-TR-001` | TR + CARD types and BFF client generated |
| `AG-FE-TR-002` | TR + candidate-decision integration contract available |
| `AG-FE-SW-001` | CARD contract available |
| `AG-FE-SW-002` | CARD + SSE contract available |
| `AG-E2E-SW-001` | E2E steps and isolation matrix merged |
| `AG-E2E-TR-001` | TR E2E assertions merged |
| `AG-TEST-ID-001` | isolation matrix merged |

## Non-design task

`AG-FE-DB-002` must not wait for v1.3. Its blocker is cross-repo delivery: the reviewed `AG-FE-DB-001` files must actually be present in `execute-plans@dev`, then the task is retried.

## Dispatch rule

No downstream task should cite a section number that exists only in a planning brief. It must cite:

- a merged prose contract path;
- a merged schema/OpenAPI path;
- the v1.3 bundle hash;
- generated frontend contract commit when relevant.
