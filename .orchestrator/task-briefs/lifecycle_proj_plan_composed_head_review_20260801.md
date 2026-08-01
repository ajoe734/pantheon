# Task Brief: LIFECYCLE-PROJ-PLAN-COMPOSED-HEAD-REVIEW-20260801

## Role and target

- Owner: `Codex`
- Independent reviewer: `Antigravity`
- Repository: `ajoe734/pantheon`
- Pull request: `#4449`
- Exact required head: `34a86e4f8e8ca9502d7a96f5753a98645a7bb46a`
- Reviewed plan ancestor: `32528f8232d14b3eaf5a2fab51c4ae532de5a4c7`
- Composed dev parent: `d2a9a6079789b6da1f15978ff7310c22a129f379`

The prior approval at `2026-08-01T15:27:22Z` is invalid. It named the dev
parent as the reviewed head and reported containment Compose tests instead of
this plan's acceptance. It must not be reused.

## Independent acceptance

1. Confirm live PR #4449 is open at exact head
   `34a86e4f8e8ca9502d7a96f5753a98645a7bb46a`.
2. Confirm that head contains both the independently approved plan commit
   `32528f8232d14b3eaf5a2fab51c4ae532de5a4c7` and dev parent
   `d2a9a6079789b6da1f15978ff7310c22a129f379`, with no conflict-resolution
   content or newly authored plan changes.
3. Confirm the PR delta is exactly the 13 archived design, execution-task,
   manifest, and incident-evidence files under the declared task artifacts.
4. Validate the seven-task DAG and the versioned `DevTaskPacket` source.
   `task-packet.source.json` must have SHA-256
   `815ef31260eede4aebabab4528865a45471f4708efbda9e7208fa175a1ac8b7f`,
   exactly seven tasks, and `signature: null`.
5. Confirm all GitHub checks on the exact head pass and `git diff --check` is
   clean.
6. Confirm there is no authority to dispatch before merge plus governed
   signing, restart the projector, delete projection state, or perform
   production/live-capital writes.

Approve only with a message that explicitly names PR #4449, exact head
`34a86e4f8e8ca9502d7a96f5753a98645a7bb46a`, seven tasks, the source-packet
SHA-256 above, and `signature: null`. Otherwise reopen with the exact gap.
