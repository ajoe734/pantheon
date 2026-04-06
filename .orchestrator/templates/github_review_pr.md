{{marker}}
# Pantheon Review Bus

## Task
- ID: `{{task_id}}`
- Title: {{task_title}}
- Summary: {{task_summary}}
- Status: `{{task_status}}`
- Owner: `{{task_owner}}`
- Reviewer: `{{task_reviewer}}`
- Depends On: {{depends_on}}

## Review Scope
{{artifacts}}

## Branching
- Head Branch: `{{branch}}`
- Base Branch: `{{base_branch}}`

## Next Step
{{next_step}}

## Mobile Review Guidance
Use GitHub Mobile PR review actions:
- `Approve`
- `Request changes`
- `Comment`

The orchestrator polls review results and writes them back into `ai-status.json`.
