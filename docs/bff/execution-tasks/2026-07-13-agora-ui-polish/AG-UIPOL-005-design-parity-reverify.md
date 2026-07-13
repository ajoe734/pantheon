# AG-UIPOL-005: Design parity re-verification against the recovered design source

## Scope

The real design source is recovered and versioned at
`docs/design/agora-trading-desk-design/` (interactive `Agora.dc.html` docs,
26 design screenshots, V2–V11 requirement documents). This supersedes the
AG-GAP-010 "lost source" baseline. This task produces the true parity matrix
and the fix backlog; it does not itself restyle the app.

## Work

1. Open the design documents and screenshots; enumerate every designed
   surface/state relevant to the three shipped tabs (trading room + proposal
   flow, strategy workshop, performance) and the servant drawer.
2. Capture current hosted screenshots of the same surfaces (desktop + narrow).
3. Produce `parity-matrix.md` next to this brief: per surface — design ref
   (file/screenshot), current state, verdict (match / minor drift / major
   drift / missing), and the concrete differences (layout, hierarchy, color,
   typography, copy, empty states).
4. File the major-drift items as follow-up task briefs (AG-UIPOL-006+ drafts)
   ranked by operator impact; do not silently fix things inside this task.
5. Coordinate with AG-UIPOL-001..004: defects already covered there are
   referenced, not duplicated.

## Acceptance

- `parity-matrix.md` merged, covering every designed surface for the three
  tabs + drawer, each row citing its design source file.
- Hosted screenshots archived beside the matrix, pinned to the deploy SHA.
- Follow-up brief drafts for every major drift, each citing matrix rows.
- No claim of "parity achieved" — this task's output is the honest gap map.
