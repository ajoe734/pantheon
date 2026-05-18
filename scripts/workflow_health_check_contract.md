# Workflow Health Check Contract

Status: task-scoped contract for `WORKFLOW-HEALTH-001`

`scripts/workflow_health_check.py` is a read-only helper for chair-review
cycles. It emits workflow-health findings for the post wave-cadence operating
model: per-task PRs into `dev`, nightly publish cuts, and publish promotion to
`master`.

## Scope

The module owns three checks:

- `check_task_pr_stale(threshold_hours=24)`: lists open PRs through
  `gh api`, filters `task/*` heads targeting `dev`, and reports any PR whose
  `updated_at` is older than the threshold.
- `check_dev_publish_stale(threshold_hours=24)`: compares the latest `dev`
  commit time with the nightly publish artifact `last_publish_at`. It reports
  when `dev` has advanced after the last publish and that advance is older than
  the threshold.
- `check_publish_promote_stale(window_hours)`: compares the last publish time
  with `master` promotion time. It reports when a publish remains unpromoted
  longer than the configured window.

## Finding Payload

Every finding is a JSON object with these required fields:

- `finding_id`
- `type`
- `severity`
- `recommended_action`
- `evidence_refs`
- `detected_at`

The module may include an `evidence` object with structured details such as PR
number, branch names, timestamps, thresholds, and release version.

## Safety

The checker must not mutate git state, create PRs, merge branches, dispatch
workflows, or edit Pantheon coordination files. `gh api` reads are allowed for
PR and commit metadata. Offline JSON inputs are supported for tests and for
chair-review prompts that already contain the relevant evidence.

## CLI

The CLI prints a JSON report:

```bash
python3 scripts/workflow_health_check.py \
  --task-pr-json /tmp/open-prs.json \
  --dev-latest-commit-at 2026-05-16T08:00:00Z \
  --last-publish-at 2026-05-16T07:00:00Z \
  --master-promoted-at 2026-05-17T09:00:00Z
```

Exit code is `0` when no findings are emitted and `1` when one or more
findings are present.
