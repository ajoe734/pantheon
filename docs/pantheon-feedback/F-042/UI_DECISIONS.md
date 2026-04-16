# F-042 Promotion Review — UI Decisions

## Contract Alignment
- **Decision**: Reject the UI-local types (`unavailable`) in favor of the canonical backend contract (`error`).
- **Rationale**: Maintain consistency across all surfaces; `error` is the platform-wide standard for degraded components.

## BFF Client
- **Decision**: Enforce strictly-typed BFF client for error handling.
- **Rationale**: The UI should not invent its own error envelope; it must consume the standard `errors` array from Pantheon.
