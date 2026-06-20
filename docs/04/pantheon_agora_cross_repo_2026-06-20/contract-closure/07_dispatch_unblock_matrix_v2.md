# Dispatch Unblock Matrix v2

## Current state

All five tasks remain `STOP` until the contract artifacts below are merged into `pantheon@dev`, generated types are mirrored to `execute-plans@dev`, and the new extension bundle verifies.

| Task | Current blocker | Required predecessor | Unblock evidence |
|---|---|---|---|
| AG-XR-003 | no manifest schema/path/hash semantics | AG-XR-001A contract coexistence | compatibility schema + example + CI validator merged |
| AG-BE-ID-002 | no ensure route/capability/adapter API | AG-XR-OPENAPI-001 | OpenAPI v1.1 + capability v1.1 + adapter contract merged |
| AG-BE-SW-001 | no workshop route family in frozen API | AG-XR-OPENAPI-001 | `/bff/agora/workshops` contract and persistence spec merged |
| AG-BE-DB-001 | incompatible widget schemas; CRUD/concurrency absent | AG-XR-DASH-001 | WidgetSpec v2, Recipe v2, ChartSpec, CRUD/ETag contract merged |
| AG-FE-DB-001 | generated types and renderer decision absent | AG-BE-DB-001 contract only, not implementation | generated v2 types + dependency decision + IA spec merged |

## New design/contract tasks

```text
AG-XR-001A
  Preserve frozen XR-001 and publish additive Agora contract extension bundle.

AG-XR-OPENAPI-001
  Publish Agora OpenAPI v1.1 and capability manifest v1.1 for servant/workshop routes.

AG-XR-DASH-001
  Publish WidgetSpec v2, ChartSpec v1, DashboardRecipe v2 and mutation/concurrency contract.

AG-XR-003
  Implement the cross-repo compatibility manifest and deployment validator.
```

## Execution order

```text
AG-XR-001A
  ├─ AG-XR-OPENAPI-001
  │    ├─ AG-BE-ID-002
  │    └─ AG-BE-SW-001
  ├─ AG-XR-DASH-001
  │    ├─ AG-BE-DB-001
  │    └─ AG-FE-DB-001
  └─ AG-XR-003
```

## Important

The design pack itself does not unblock workers. Unblocking occurs only after these artifacts are committed, reviewed, hashed and type-generated on the two `dev` branches.
