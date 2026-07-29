# Dev Deployment Evidence: AG-GOV-WORKSHOP-COMPAT-DEPLOY-001

## Outcome

- Task owner: `Codex`
- Reviewer: `Claude2`
- Environment: Pantheon dev
- Result: accepted
- Backend runtime: `f71c1f8ba889ba64956006ef0f9159840be6d065`
- Frontend runtime: `e4399e3ec68f882ace35d0349e6597cdd101525f`
- Compatibility manifest SHA-256:
  `d61e11cf2cead97d4a66ab153a2081ef4d633671ee4f962d271a7b3feeb86867`
- Raw redacted evidence:
  `docs/deployment/evidence/agora/ag-gov-workshop-compat-deploy-001/qualification-20260724T045953Z.json`

The repaired Governance–Workshop backend is served with strict authentication,
and the public frontend deployment manifest binds the exact accepted frontend
and backend commits. The final hosted probe created and approved a canonical
`strategy_workshop` target, preserved distinct Registry and strategy identities,
used handoff-only research, concluded the Workshop, restarted the BFF through
the governed workflow, and read every durable resource back successfully.

## Accepted contract and handoff identities

- Contract commit:
  `9e909de182f9f2379d23e8e6b81eefec29ffbce7`
- Bundle index SHA-256:
  `b1d488c3b35aa1c691e5b464362ac5a2fdd1efc442249e15be9bb143f379f870`
- OpenAPI SHA-256:
  `36d1be5bc033ea1a55610f3f523fc478704fdfad1f06fec620e741bed9bf6f86`
- Capability manifest SHA-256:
  `7dfddaf220c00eddb7cbd0862eaa6f2aba7423dbd02e54d15db1d67a0cb4ded1`
- Backend handoff SHA-256:
  `8510946b40ec2adc11788dc40be7cd8db9fc824184c8b1faabe3e0f62f29312b`
- Frontend handoff SHA-256:
  `5fa6c75ae6e8c044c038570a7765522fa145c1b603cc66e4db72bdf6898b3f2b`

The bundle and handoff hashes remained byte-identical, so no frontend type
regeneration was required. The local deployment gate resolved backend tree
`963dbd087ebf608cf5533c4047f6b5fb908fd6fd` and frontend tree
`064ffbae400918aaa359902a3472e74cc4f752fa`.

## Governed workflow evidence

1. Pantheon BFF deployment
   [run 30065241892](https://github.com/ajoe734/pantheon/actions/runs/30065241892)
   succeeded with target ref `f71c1f8ba889ba64956006ef0f9159840be6d065`,
   `auth_stub=false`, and strict authentication.
2. Execute Plans integration gate
   [run 30003411349](https://github.com/ajoe734/execute-plans/actions/runs/30003411349)
   completed successfully for frontend
   `e4399e3ec68f882ace35d0349e6597cdd101525f` and the accepted backend.
3. The read-only frontend release
   [run 30067684910](https://github.com/ajoe734/execute-plans/actions/runs/30067684910)
   passed the immutable-artifact check, exact-pair gate, controller regression,
   pre-switch browser probe, switch, post-switch probe, and evidence seal.
4. After the task probe created its durable resources, Pantheon BFF restart
   [run 30068077516](https://github.com/ajoe734/pantheon/actions/runs/30068077516)
   again passed the exact-pair gate, strict auth floor, public version proof,
   generic Agora restart-persistence smoke, and identity-bound lease release.

The public frontend manifest after the accepted switch reports:

- Pair ID:
  `ec91a4aaaee16719f6db6a3d7b6edba048c08e676d789bfb9301df92913c3de2`
- Release:
  `20260724T045319Z-e4399e3ec68f-gate-30003411349-30067684910-1-3592355`
- State/profile: `accepted` / `read-only`
- `VITE_BFF_MODE=live`
- `VITE_BFF_FALLBACK=strict`
- `VITE_BFF_REAL_WRITES=false`
- `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`
- Embedded bearer token: `false`

## Hosted Governance–Workshop repair proof

The seed phase completed at `2026-07-24T04:56:03Z` with these durable IDs:

- Strategy:
  `strategy-ag-gov-workshop-20260724T045602Z-633423`
- Initial Registry entry:
  `registry-ag-gov-workshop-20260724T045602Z-633423`
- Version Registry entry:
  `reg-ws-557d2e781d7c06e8cebb`
- Workshop:
  `fdf40067-8fa6-45b4-8d55-f78f07b8bde5`
- Workshop version:
  `wsv-557d2e781d7c06e8cebb`
- Approval:
  `approval-ag-gov-workshop-20260724T045602Z-633423`

The probe demonstrated that the two Registry IDs differ from the strategy ID
and that the version Registry entry still points to that strategy. Governance
accepted `target_type=strategy_workshop`, bound the approval to the Workshop
and version, and recorded an approved decision from the separate dev approver.
The Workshop research route returned 202 in `handoff_only` mode and conclusion
returned 200 with the selected Registry and strategy identities intact.

After governed run `30068077516` restarted the BFF, the verification phase
completed at `2026-07-24T04:59:53Z`. It read back both Registry entries, the
decided canonical approval, and the concluded Workshop, and rechecked the
public exact pair and safe frontend defaults.

Safety assertions were true in both phases:

- no backing store was edited directly;
- no credential or token was emitted;
- execution authority was `none`;
- no deployment, order, broker, allocation, or capital API was called;
- research mode was `handoff_only`;
- no live capital was involved.

## Fail-closed observations

- Root deployment
  [run 30065717086](https://github.com/ajoe734/pantheon/actions/runs/30065717086)
  updated the Governance, Registry, and BFF services, but its first attempt
  failed on the BFF readiness window and its second attempt was externally
  cancelled during image export. Neither attempt is treated as acceptance
  evidence.
- Frontend run
  [30066014879](https://github.com/ajoe734/execute-plans/actions/runs/30066014879)
  failed before switch while the root run was recreating the BFF, and then
  failed before switch on a stale lifecycle projector. In both cases the
  previous symlink remained served.
- An initial bounded live recovery restarted only
  `loop-run-projector-scheduler` after the cancelled root build left its
  publication stale. The worker completed recovery, returned to live mode, and
  resumed generation advancement with zero backlog.
- A separate persistent live repair from
  [PR #4043](https://github.com/ajoe734/pantheon/pull/4043) then recreated only
  `operator-bff`, preserved strict auth and exact source SHA, and raised the
  managed-dev lifecycle freshness budget to 300 seconds. That repair did not
  edit backing stores. Public `/readyz` remained 200 before accepted frontend
  run `30067684910` and after strict BFF restart run `30068077516`.

## Local validation

- Agora deployment manifest and isolation tests:
  `19 passed` in 14.33 seconds.
- Governance decision, Governance API, Workshop, and live-operation tests:
  `180 passed, 5 skipped` in 243.45 seconds.
- Full Agora suite from the manifest update:
  `466 passed, 8 skipped` in 271.44 seconds.
- Contract bundle `--check`: passed.
- Frontend handoff verification: passed.
- Exact deployment manifest verification and deployment gate: passed.
- Hosted probe Python compilation and Ruff check: passed.
