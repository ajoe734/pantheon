# Evolution Programs — Current Contract Characterization

Task: ACG-BFF-EVOEXP-PREP-20260828 (design unit ACG-01-EVOEXP).
Matrix items: ACG-01-006 (MERGE), ACG-01-007 (REMOVE, follow-up task).

This document characterizes the route family exactly as it behaves today in
`services/control-plane/bff/main.py`, before any wiring change, so the
prepared router in `router.py` can be reviewed against a known-true baseline
and so the follow-up cutover task has a single reference for what must not
regress.

## Current duplicate registration (as-is, not touched by this task)

`main.py` registers `/bff/evolution-programs*` twice:

- **Durable block** (`main.py:57248-57447`): reads/writes go straight to
  `read_store.list_evolution_programs` / `create_evolution_program` /
  `get_evolution_program` / `patch_evolution_program` /
  `list_evolution_program_runs` / `list_evolution_program_candidates`.
  Envelope uses a `data` key for lists/detail.
- **`_GOV_BFF` overlay block** (`main.py:60916-61186`): reads merge
  `read_store.list_evolution_programs()` with an in-process dict
  `_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY`; writes (create/patch) go **only** to
  the overlay dict, never to `read_store` — they do not survive a restart.
  Envelope uses an `items` key for lists.

`_prefer_latest_bff_gap004_routes()` (`main.py:61698-61727`) prunes the
earlier (durable) route registrations from `app.router.routes` for every
path in `_BFF_GAP004_ROUTE_PATHS`, so **the overlay block is what actually
serves traffic today**; the durable block is registered then discarded at
import time. This is the ACG-01-006 defect: the "canonical_owner" is
supposed to be the durable path, but the live behavior is the non-durable
overlay.

## Six normalized route shapes (the ACG-01-006 gate)

| Method | Path | Live handler today |
|---|---|---|
| GET | `/bff/evolution-programs` | overlay (`main.py:60916`) |
| POST | `/bff/evolution-programs` | overlay (`main.py:60948`) |
| GET | `/bff/evolution-programs/{id}` | overlay (`main.py:61000`) |
| PATCH | `/bff/evolution-programs/{id}` | overlay (`main.py:61026`) |
| GET | `/bff/evolution-programs/{id}/runs` | overlay (`main.py:61067`) |
| GET | `/bff/evolution-programs/{id}/candidates` | overlay (`main.py:61121`) |
| POST | `/bff/evolution-programs/{id}/actions/{action_id}` | overlay (`main.py:61156`) |

(`GET /bff/evolution-programs/{id}/ooda` at `main.py:46710` is a single,
already-durable registration outside the duplicate pair; it is not part of
this router's scope.)

## Envelope contract (what `evolution/router.py` preserves)

- **List** (`GET /bff/evolution-programs`, `.../runs`, `.../candidates`):
  `{"items": [...], "page_info": {"next_page_token": str | None}, "meta": {...}}`.
  This matches the *live* overlay shape (`items`, not the dead durable
  block's `data`) because that is the shape existing consumers and
  `test_bff_evolution_experiment_jobs_events_contract.py` depend on today.
- **Create / Patch** (`POST` / `PATCH /bff/evolution-programs[/{id}]`):
  flat record dict (both the durable and overlay blocks return a flat
  dict here, not a `data`-wrapped envelope — no divergence to resolve).
- **Detail** (`GET /bff/evolution-programs/{id}`):
  `{"data": {...}, "meta": {...}}` (both blocks agree on this shape).
- **Action** (`POST .../actions/{action_id}`): delegated to the injected
  `submit_program_action` callable (see below); response shape is whatever
  the command-store/audit pipeline in main.py already returns.

## Filters, pagination, validation

- `status` filter: comma-separated, case-insensitive set membership against
  `record["status"]` (the overlay's contract; the dead durable block only
  supported a single exact-case value and is not preserved here).
- Pagination: opaque numeric-offset token (`page_token` = decimal offset as
  a string), `page_size` defaults to 20, bounded `[1, 200]` — identical in
  both existing blocks.
- Sort order for list responses: `created_at` descending (both blocks
  agree).
- `POST /bff/evolution-programs` validation: `name` must be a non-empty
  string after `.strip()`, else `422 VALIDATION_FAILED` (both blocks
  agree). No other field is validated in either existing block.
- `PATCH /bff/evolution-programs/{id}` only applies `name`/`status`/`params`
  from the payload; other fields are silently ignored (both blocks agree;
  this router follows `read_store.patch_evolution_program`'s own
  whitelist, which limits to the same three fields).

## Deliberate divergence from the (dead) durable block

The durable block guards create/patch/action with `_require_read_role`
instead of `_require_operator_role`. This router follows the **live**
overlay's `_require_operator_role` for all mutations, since that is the
behavior actually in effect today and the one existing consumers have been
built against.

## What still needs main.py at wiring time (not reimplemented here)

- `submit_program_action`: the actual action-dispatch pipeline
  (`_gov_bff_action_command` / `_evol_exp_bff_action_command`) — command
  IDs, idempotency-key conflict handling, foundation audit records, and
  `command_adapters/evolution_adapter.py` dispatch. This router raises
  `501 NOT_IMPLEMENTED` if no callable is injected, so it fails closed
  rather than silently accepting actions with no real effect.
- `dataset_surface_status` / `snapshot_meta`: main.py's real
  `_dataset_surface_status` / `_snapshot_meta` compute richer
  staleness/telemetry metadata than the defaults here; the defaults exist
  only so this module is importable and testable standalone. Wiring must
  pass the real implementations for parity.
- Idempotency-Key / X-Idempotency-Key header handling and the
  `_GOV_BFF_IDEMPOTENCY` cross-route replay cache are intentionally not
  reproduced in `create_evolution_program` / `patch_evolution_program`
  here; the cutover task should decide whether to inject that cache too or
  rely on `read_store.create_evolution_program`'s own idempotence (it has
  none today — a second POST with the same `program_id` overwrites the
  first record, in both the current durable block and this router).
