#!/usr/bin/env python3
"""Reproduce PR #4399's dispatch/preemption provider-readiness mismatch."""

from __future__ import annotations

import json

import supervisor


config = {
    "schema": {
        "assignee_field": "owner",
        "reviewer_field": "reviewer",
    },
    "ready_dispatcher": {
        "active_worker_statuses": ["running"],
        "worker_os_duplicate_guard": False,
    },
    "agents": {
        "codex": {
            "id": "codex",
            "display_name": "Codex",
            "provider": "codex",
        }
    },
    "providers": {"codex": {"delivery_mode": "codex"}},
}
provider_report = {
    "providers": {
        "codex": {
            "local_cli_worker_supported": False,
            "supports_auto_approve": False,
            "auth_ready": True,
        }
    },
    "agent_adapters": {
        "codex": {
            "supported": True,
            "can_auto_deliver": True,
        }
    },
}
worker = {
    "run_id": "run-current",
    "task_id": "CURRENT",
    "agent_id": "codex",
    "status": "running",
    "request_snapshot": {"reason": "owned_ready_dispatch"},
}
state = {
    "queue": {"events": {}},
    "workers": {worker["run_id"]: worker},
    "seen_event_keys": {},
}
task_map = {
    "CURRENT": {
        "id": "CURRENT",
        "status": "todo",
        "owner": "Codex",
        "reviewer": "Codex2",
        "depends_on": [],
    },
    "URGENT": {
        "id": "URGENT",
        "status": "review",
        "owner": "Codex2",
        "reviewer": "Codex",
        "depends_on": [],
    },
}

# Make both paths observe the same report. The bug is that agent_can_take_task,
# used by preemption, checks only auth readiness from this cached payload.
supervisor._cached_provider_capabilities = lambda _config: provider_report
supervisor.load_event_queue = lambda _config: []

dispatch_block_reason = supervisor.agent_auto_dispatch_block_reason(
    config,
    state,
    "codex",
    provider_report,
)
preemption_decision = supervisor.higher_priority_ready_task_exists(
    config,
    worker,
    task_map,
    state,
)
print(
    json.dumps(
        {
            "dispatch_block_reason": dispatch_block_reason,
            "preemption_decision": preemption_decision,
        },
        sort_keys=True,
    )
)

assert not preemption_decision, (
    "preemption must not kill a worker for a provider that dispatch refuses"
)
