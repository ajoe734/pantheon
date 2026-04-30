# Review: SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE

Reviewer: Codex
Date: 2026-04-30
Status: approved after follow-up

## Follow-up Review

Claude addressed the blocking finding in commit `fe768f5` by adding
`_ALWAYS_BLOCKED_TOOL_PREFIXES`, lowercasing `tool_name` before always-blocked
checks, applying dotted-prefix rejection before allowlist lookup, and adding
regression coverage for dotted and case-varied dangerous tool refs.

Verification run by Codex after the follow-up:

```bash
PYTHONPATH=services/openclaw-gateway-adapter python3 -c "from tool_workflow_bridge import ToolPolicy; p=ToolPolicy(allowed_tools=['broker.submit','live.trade','paper.backtest','capital.bind_pool']); [print(name, p.evaluate_tool(name).allowed, p.evaluate_tool(name).policy_class) for name in ['broker.submit','live.trade','paper.backtest','capital.bind_pool']]"
python3 -m compileall -q services/openclaw-gateway-adapter
python3 -m pytest services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q
python3 -m pytest services/openclaw-gateway-adapter -q
```

Results:

- reproduction now returns `False always_blocked` for `broker.submit`,
  `live.trade`, `paper.backtest`, and `capital.bind_pool`
- compile clean
- 56 bridge tests passed
- 119 adapter tests passed

Disposition: approved. Owner should finalize through the normal
`review_approved -> done` closeout flow.

## Findings

1. `services/openclaw-gateway-adapter/tool_workflow_bridge.py:169`
   Tool policy only checks exact names in `_ALWAYS_BLOCKED_TOOLS`, while workflow policy correctly blocks dangerous prefixes. This allows broker/live/paper/capital tool names with dotted or namespace-style refs to pass when present in `OPENCLAW_ALLOWED_TOOLS`.

   Reproduction:

   ```bash
   PYTHONPATH=services/openclaw-gateway-adapter python3 -c "from tool_workflow_bridge import ToolPolicy; p=ToolPolicy(allowed_tools=['broker.submit','live.trade','paper.backtest','capital.bind_pool']); [print(name, p.evaluate_tool(name).allowed, p.evaluate_tool(name).policy_class) for name in ['broker.submit','live.trade','paper.backtest','capital.bind_pool']]"
   ```

   Current output:

   ```text
   broker.submit True allowlist
   live.trade True allowlist
   paper.backtest True allowlist
   capital.bind_pool True allowlist
   ```

   This violates the acceptance criterion that broker/paper/live execution is not enabled and that broker/live/paper/capital tool paths remain fail-closed regardless of allowlist. Fix by applying the same namespace/prefix protection to tool names, normalizing case, and adding regression tests for dotted and case-varied dangerous tool names.

## Verification Run

```bash
python3 -m compileall -q services/openclaw-gateway-adapter
python3 -m pytest services/openclaw-gateway-adapter -q
python3 -m pytest services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q
```

Results:

- compile clean
- 114 adapter tests passed
- 51 bridge tests passed

Existing tests pass but do not cover the dotted/namespace tool-name bypass above.
