# Review: CONSOLE-DATA-KNOWLEDGE-RESEARCH

Reviewer: Claude
Date: 2026-06-15
Task: Populate /bff/knowledge, /bff/research-analyses, /bff/research/tasks

## Verdict: APPROVED

## Scope Reviewed

- `scripts/project_research_to_bff_surfaces.py` — extended projection script
- `services/control-plane/bff/tests/test_console_research_projection.py` — new BFF test coverage
- `docker-compose.yml` — added BFF store env vars and memory service wiring
- `.orchestrator/task-briefs/console_data_knowledge_research.md` — task brief

PR #1690 merged at 5a92e2fe / anchor e1902e6c.

## Findings

**Projection script (637 lines total after extension):**
- Adds clean, well-typed helper functions (_get, _items, _mapping, _first_text, etc.)
- `project(ro_url, memory_url)` reads research-orchestrator tasks/runs/artifacts and memory-svc entries — no fabricated rows
- `write_projection(stores, out_dir)` writes all store files atomically
- All projection paths handle missing/empty payloads gracefully
- StoreMap type alias makes the data flow clear

**Tests:**
- `test_projected_research_console_surfaces_return_ok_counts` covers all three BFF surfaces
- Fake service payloads are realistic (stub adapter, completed status, proper timestamps)
- Asserts count>0 and surface status=ok for /bff/knowledge, /bff/research-analyses, /bff/research/tasks
- Verifies specific IDs (analysis-rrun-console-001, rtask-console-001) round-trip correctly

**Docker-compose:**
- Adds 6 new BFF store env vars for research/knowledge surfaces
- Wires PANTHEON_MEMORY_API_URL and memory service dependency correctly
- PANTHEON_MEMORY_DATA_DIR added for memory service

**Scope boundary respected:**
- Research-orchestrator dispatch safety: unchanged
- Production adapter gating: unchanged
- BFF auth policy: unchanged
- Frontend routing: unchanged
- Canonical architecture docs: unchanged

## Verification Evidence (from owner)

- `python3 -m py_compile scripts/project_research_to_bff_surfaces.py` ✓
- pytest 15 passed ✓
- `docker compose config --quiet` ✓
- Live curls: knowledge total=8 status=ok, research-analyses total=2 status=ok, research/tasks total=1 status=ok ✓
- CI checks passed: commit trailers, runtime mirror guard, smoke acceptance, orchestrator sync ✓

## Conclusion

Implementation is clean, no fabricated data, acceptance criteria (三面 count>0) met, CI green. Approved for owner finalization.
