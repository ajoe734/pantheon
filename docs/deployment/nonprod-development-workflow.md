# Non-Prod Development Workflow

Status date: 2026-09-11

The current environment identities and deployment variables are maintained in
[the VM management plan, § 3.1](vm-dev-staging-prod-management-plan.md#31-dev).
This guide does not define a second host, account, URL, or environment table.
The former June 2026 VM commands are retired; consult Git history only for
historical evidence, never as a deployment target.

## Development and delivery

1. Work in a clean task branch or worktree and run the relevant local checks.
2. Deliver the exact tested source to `dev` through the repository's applicable
   tooling or product workflow. A workstation checkout is not a deployed BFF.
3. For authorized product dev delivery, use the existing `Pantheon Nonprod
   Deploy` workflow and current GitHub environment configuration. Keep the
   exact FE/BFF candidate pair, gate-before-switch, served-identity readback
   and retained-artifact rollback; do not replace these with ad hoc rebuilds.
4. For frontend work use the separate `ajoe734/execute-plans` repository and
   [its dev hosting runbook](../frontend/execute-plans-dev-hosting.md).
   Lovable publish status is not Pantheon dev acceptance evidence.

## Minimal boundaries

- Keep dev broker/capital writes disabled. Do not put broker credentials or
  live broker state on a workstation or in frontend assets.
- Use HTTPS and the configured BFF CORS origin for browser access. Configured
  DNS/URLs alone do not prove that the expected product version is served.
- Staging and production have separate readiness and authorization; read
  §§ 3.2–3.3 of the VM management plan. Retired staging topology and examples
  must not be used as live commands or to infer available capacity.
- Updating development tooling does not deploy the product or release an
  operator's stop/hold. Leave staging, production and stopped product work
  untouched unless the operator explicitly includes them.
