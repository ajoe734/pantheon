# Dashboard Recipe CRUD and Optimistic Concurrency

## Canonical routes

```text
GET  /bff/agora/strategies/{strategy_id}/dashboard-recipes
POST /bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals
GET  /bff/agora/dashboard-recipes/{recipe_id}
POST /bff/agora/dashboard-recipes/{recipe_id}/accept
PATCH /bff/agora/dashboard-recipes/{recipe_id}/layout
POST /bff/agora/dashboard-recipes/{recipe_id}/rollback
POST /bff/agora/dashboard-recipes/{recipe_id}/feedback
GET  /bff/agora/dashboard-recipes/{recipe_id}/versions
POST /bff/agora/widgets/validate
POST /bff/agora/widgets/{widget_id}/feedback
POST /bff/agora/widgets/propose-plugin
```

## Version model

Dashboard versions are append-only.

```text
dashboard_recipe_identity
  recipe_id PK
  tenant_id
  user_id
  strategy_id
  active_version
  created_at

dashboard_recipe_version
  recipe_id
  version
  previous_version
  status
  recipe_json
  content_sha256
  generated_by
  change_reason
  created_at
  PRIMARY KEY(recipe_id, version)
```

No route overwrites a historical version.

## ETag

GET returns:

```text
ETag: "recipe:<recipe_id>:v<version>:<content_sha256-prefix>"
```

Every state-changing request requires:

```text
If-Match: <etag from latest GET>
Idempotency-Key: <client-generated UUID>
```

The request body also contains `expected_version` for explicit auditability.

On mismatch:

```json
{
  "error": {
    "code": "CONCURRENT_MODIFICATION",
    "message": "Dashboard recipe changed after the client snapshot.",
    "details": {
      "expected_version": 4,
      "current_version": 5,
      "current_etag": "recipe:rec_1:v5:abcd1234",
      "latest_href": "/bff/agora/dashboard-recipes/rec_1"
    }
  }
}
```

## Mutation semantics

### Proposal

Creates a proposal version; does not replace the active version.

### Accept

Atomically marks the proposal accepted, creates/activates the next immutable version and moves the active pointer.

### Layout patch

Accepts JSON Patch-like operations limited to:

```text
move_widget
resize_widget
remove_widget
add_registered_widget
replace_chart_spec
update_widget_query
```

Each operation is validated against WidgetRegistry, data scope and layout bounds.

### Rollback

Rollback creates a new version whose content equals a selected historical version. It never rewinds or deletes history.

### Feedback

Feedback is append-only and does not mutate the recipe unless a later accepted proposal is created.

## Capability

Add `agora.dashboard.v2` to the v1.1 capability manifest. New Trading Desk clients require this capability; legacy screens may remain on `agora.dashboard.v1`.
