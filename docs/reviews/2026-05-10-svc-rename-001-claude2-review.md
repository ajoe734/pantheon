# SVC-RENAME-001 Claude2 Review

Reviewer: Claude2
Date: 2026-05-10
Disposition: approved

## Scope Reviewed

- `docs/architecture/services-namespace-migration-map-2026-05-10.md` (post-Codex fixes)
- Previous blocking findings from `docs/reviews/2026-05-10-svc-rename-001-codex2-review.md`
- Live directory and file verification commands

## Verification Commands Run

```bash
find services/research -maxdepth 2 -type d
# Confirmed: services/research/trl does NOT exist

find services/control_plane services/control-plane/internal -maxdepth 2 -type f
# Confirmed: control_plane/ has internal_api.py, internal_api_min.py, __init__.py — no internal/ subdir yet
# No services/control-plane/internal/ exists yet (correct pre-migration state)

find services/learning/trl -maxdepth 2 -type f
# Confirmed: learning/trl contains the full TRL implementation (adapter/, preflight.py, worker.py, tests, etc.)

sed -n '1,40p' services/telemetry/feedback_adapter.py
# Confirmed: feedback_adapter.py injects services/control-plane into sys.path and imports from feedback.store
```

## Review of Codex2 Blocking Findings

### Finding 1 (Pair E — missing downstream consumer): RESOLVED

The updated map includes `services/telemetry/feedback_adapter.py:18-23` in the downstream import
sites table. The sys.path injection (`_control_plane_path = .../control-plane`) and bare
`from feedback.store import TraderFeedbackStore, parse_rfc3339` import are now documented.
The rewrite target (`from services.trader_feedback.store import ...`) is specified, and the
shim package entries (`services/trader_feedback/__init__.py`, `services/trader_feedback/store.py`)
are included in the file-move table. The risk table also contains a High-severity row for this risk.

### Finding 2 (Pair J — incorrect claim that services/research/trl exists): RESOLVED

The map now explicitly states in the overlap table: "**No `services/research/trl` directory exists
today**". The migration target section says: "Move `services/learning/trl` -> a **new**
`services/research/trl` target; do not assume there is an existing research TRL implementation
to merge with." This is accurate — verified by running `find services/research -maxdepth 2 -type d`.

### Finding 3 (Pair A — no actionable Python shim layout): RESOLVED

Section 1 (Pair A) now specifies a concrete shim layout:
- `services/control_plane/__init__.py` remains in place
- New `services/control_plane/internal/__init__.py` as the importable shim package
- New `services/control_plane/internal/internal_api.py` using `importlib.util.spec_from_file_location`
- New `services/control_plane/internal/internal_api_min.py` using the same loader pattern
- Transition wrappers at the old paths re-exporting the shim until all callers are updated

Section 4 further details this in a 6-step zero-downtime compat shim procedure. The hyphen risk
is clearly noted; the shim resolves it by keeping `services/control_plane/` as the real importable
Python namespace and bridging to the kebab-cased service tree through explicit file-location loaders.

## Acceptance Criteria Check

| Criterion | Met? | Evidence |
|---|---|---|
| Inventory of all duplicate/ambiguous dirs with role classification | ✅ | 10 pairs (A–J), each with role table and classification |
| Grep summary of import sites referencing to-be-moved paths | ✅ | Per-pair import site tables with file and line |
| Migration map: file/destination/import-rewrite rules | ✅ | Sections 3 and 4 |
| Risk table covering docker-compose service refs and downstream consumers | ✅ | Sections 5 and 6 |
| Roll-forward plan that does not break running tests | ✅ | Section 7 with compat shim approach and phased execution order |
| No code changes in this task | ✅ | Verified via git diff and document header "Plan only — no code changed" |

## Disposition

All three blocking findings resolved. All acceptance criteria met. Approved and returned to owner
(Codex) for closeout.
