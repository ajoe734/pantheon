# PPL-ALLOC-005 - Frontend Create Paper Persona Flow

Owner: Codex2
Reviewer: Claude
Depends on: `PPL-ALLOC-002`, `PPL-ALLOC-003`
Type: frontend implementation task

## Problem

The Persona registry still behaves like a generic object list. Operators need
one creation flow that creates a runnable paper persona bundle and makes any
incomplete setup explicit.

## Scope

- Replace the generic persona create drawer action with Create Paper Persona.
- Collect persona, mandate, strategy direction, data sources, and risk
  preference in one flow.
- Submit to the BFF create-paper-bundle command.
- On success, navigate to Persona Fleet or Persona Detail showing
  `paper_running`, paper ledger, runtime binding, and next evaluation.
- On partial failure, navigate to setup repair with the exact failed step.
- Reclassify `PersonaOnboarding` as setup repair / completion, not the normal
  creation path.

## Acceptance

- Component tests cover success, partial failure, write-disabled state, and BFF
  error.
- The create flow never reports success without `paper_ledger_id` and
  `runtime_binding_id`.
- Persona registry copy and navigation do not imply a passive draft trading
  persona.
- Accessibility and mobile layout tests cover the creation flow.

## Validation

```sh
git status -sb
npm test -- src/management/pages/PersonaCreatePaperFlow.test.tsx
npm test -- src/management/pages/oversight/PersonaFleetPage.test.tsx
npm run lint
npm run build
git diff --check
```
