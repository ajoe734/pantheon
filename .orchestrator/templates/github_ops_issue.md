{{marker}}
# Pantheon Ops Bus

## Task
- ID: `{{task_id}}`
- Title: {{task_title}}
- Summary: {{task_summary}}
- Status: `{{task_status}}`
- Owner: `{{task_owner}}`
- Reviewer: `{{task_reviewer}}`
- Depends On: {{depends_on}}

## Why This Issue Exists
- Reason: {{reason}}
- Details: {{details}}
- Next Step: {{next_step}}

## Mobile Commands
Leave one command on the first line of a comment:

- `/approve {{task_id}}`
- `/deny {{task_id}}`
- `/retry {{task_id}}`
- `/status {{task_id}}`
- `/resume {{task_owner}}`
- `/recheck {{task_id}}`

Only allowlisted GitHub users are applied by the bus.
