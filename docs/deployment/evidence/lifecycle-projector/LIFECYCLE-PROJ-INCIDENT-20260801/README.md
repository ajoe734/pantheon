# Lifecycle Projector Incident Baseline — 2026-08-01

Status: redacted incident evidence; not a fix or deployment acceptance claim

Machine-readable snapshot: [evidence.json](evidence.json)

At `2026-08-01T09:17:32Z`, the unbounded dev lifecycle projector was
deliberately stopped after it reached approximately 11.2 GiB RSS and sustained
high CPU while rebuilding its file-backed projection. Docker needed the
30-second stop timeout and recorded exit 137; `OOMKilled` is false.

No projection data was deleted. At `2026-08-01T09:27:10Z` the container was
still exited, the host had 41 GiB available, and operator BFF remained healthy.
At `2026-08-01T09:27:35Z`, BFF `/healthz` correctly returned degraded/not-ready
because the projector's last poll was stale. The last atomic read bundle remains
readable but must not be represented as fresh.

The permanent containment candidate is PR #4448 at exact head
`85e835448f7b86ce77ad9e4e0cc80961879b29c0`. Its visible checks passed, but at
capture time it had no independent review and remained blocked. The canonical
supervisor review task is `LIFECYCLE-PROJ-HOTFIX-REVIEW-20260801`.

The source observations establish that batch size is not the root cause. The
resident object model and publish algorithm grow with all canonical history and
multiply memory during deep copy, full rebuild, and JSON serialization.
