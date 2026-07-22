# AG-DYNUI-LIVE-DEFAULT-001 BFF and Frontend Handoff Packet — Follow-up 3

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-DEFAULT-001` |
| Parent title | Fix live Agora Trading Room default route visual parity |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar task | `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Prior sidecars | `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF` (done, PR #2746); `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done, PR #2747) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-02` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, or `execute-plans` frontend code in either repo
copy discussed below. Parent ownership and review decide how to act on this
packet.

---

## 1. Why This Follow-up Exists

`ai-status.json` shows the parent task's last recorded activity is still the
`progress` note at `2026-07-02T04:53:37Z` — the "missing agora dist / Caddy
routing" theory that the prior follow-up (`...FOLLOWUP-2`) already
re-verified and found not supported by Copy B's actual build architecture
(single `vite build`, no `dist/agora` split, Caddy template matches a
single-bundle deploy). No parent commit, branch, or PR has appeared in
`ajoe734/execute-plans` since then, and the live `deployment.json` is still
unchanged (`commit: 4b0b30c0...`). The parent has not yet acted on either
prior packet's recommendation.

Rather than re-litigate the same diagnosis a third time, this packet assumes
the prior packets' conclusion stands (implement in Copy B, recolor the
existing light components) and instead does the concrete engineering
groundwork the parent still needs before touching code: it identifies the
**exact styling mechanism already available in Copy B** to do the dark
recolor correctly, and flags a **contract drift trap** that could send the
parent down a dead-end theming path.

---

## 2. New Finding: Copy B Already Ships An Unused Dark Theme — Use It, Don't Invent One

Cloned `ajoe734/execute-plans` `origin/dev` (`4b0b30c010b4158dded4cb77fdbb13c057f59536`,
unchanged since the prior packet) fresh into a scratch checkout for this
packet and read the design-system files directly.

### 2.1 The token system

`tailwind.config.ts` declares `darkMode: ["class"]` and a full semantic color
palette backed by CSS variables (`background`, `foreground`, `card`,
`primary`, `secondary`, `muted`, `accent`, `destructive`, `border`, `env-*`,
`risk-*`, `bucket-*`, `status-*`, `sidebar-*`).

`src/index.css` defines **both** halves of that palette already:

```css
:root {
  --background: 220 20% 98%;   /* light dense console */
  --foreground: 222 47% 11%;
  --card: 0 0% 100%;
  ...
}
.dark {
  --background: 222 47% 6%;    /* near-black dark console */
  --foreground: 210 40% 96%;
  --card: 222 47% 9%;
  --sidebar-background: 222 50% 4%;
  --risk-critical: 0 72% 62%;  /* risk/status/env tokens re-tuned +10% lightness for dark */
  ...
}
```

This `.dark` block is a complete, already-tuned dark palette — background,
foreground, card, borders, and every semantic risk/status/env color has a
dark-mode variant, produced for exactly this kind of dense trading-desk
surface. **Nothing here needs to be invented.** It matches the "dark/neutral,
semantic-color-driven, institutional" requirement from the `AG-FE-DYNUI-005`
design pack (`V6_MultiStrategy_Dashboard` requirement doc, cited in
`support/sidecars/AG-FE-DYNUI-005/AG-FE-DYNUI-005-SIDECAR-ACCEPTANCE.md` §2)
far more precisely than porting Copy A's ad hoc `shellBg: "#111417"` inline
constant would.

### 2.2 The token system is defined but never activated

```
$ grep -rn 'classList.add("dark")\|className="dark"\|data-theme' src/App.tsx src/main.tsx index.html src/routes/agora.tsx
(no matches)
```

No component anywhere in Copy B ever applies the `dark` class to `<html>`,
`<body>`, or any ancestor. The `.dark {}` CSS block in `index.css` is dead
weight today — present, correct, and completely unused. This explains why
*every* Agora surface in Copy B is light, not just `TradingRoomPage`:

```
$ grep -rn "bg-slate-900\|bg-zinc-900\|bg-neutral-900\|bg-gray-900\|#111417" src/agora
(no matches across all 41 .tsx files under src/agora)
```

`StrategyWorkshopPage.tsx` and every other Agora page are equally
undelivered — this confirms `AG-FE-DYNUI-005` ("design-pack visual parity")
never actually landed in Copy B at all, only in the Pantheon-tracked mirror
(Copy A), exactly repeating the same composition gap the original packet
diagnosed for `TradingDeskLayout`/`TradingRoomPage` specifically. **The
default-route bug this parent task owns is the visible tip of a larger,
repo-wide gap**; see §5 for how to keep that distinction clean in scope.

### 2.3 Recommended mechanism, concretely

1. Apply the `dark` class to the Agora shell root (e.g. `<div
   className="dark">` wrapping `TradingDeskLayout`'s outer container, or the
   `AgoraLayoutRoute` that mounts it in `src/routes/agora.tsx`) rather than
   globally on `<html>` — Management console (`/management/*`) should stay on
   the light palette; nothing in the design pack or prior packets asks for
   Management to go dark.
2. In `TradingDeskLayout.tsx`, swap the hardcoded light Tailwind utility
   classes for the semantic ones already wired to the CSS variables:

   | Element | Current (light-only) | Replace with |
   |---|---|---|
   | `CommandBar` `<header>` | `border-b border-slate-200 bg-white` | `border-b border-border bg-background` |
   | `CommandBar` title `<span>` | `text-slate-900` | `text-foreground` |
   | `TabBar` `<nav>` | `border-b border-slate-200 bg-white` | `border-b border-border bg-background` |
   | `TabBar` inactive tab | `text-slate-600 hover:text-slate-900` | `text-muted-foreground hover:text-foreground` |
   | `ServantDrawer` `<aside>` | `border-l border-slate-200 bg-white` | `border-l border-border bg-card` |
   | `BottomStrip` `<footer>` | `border-t border-slate-200 bg-slate-50` | `border-t border-border bg-muted` |

   This is a mechanical class swap because `TradingDeskLayout.tsx` already
   uses Tailwind utility classes end to end (via the `cn()` helper) — no
   markup restructuring needed.
3. `TradingRoomPage.tsx` is a different shape: it uses inline `style={{...}}`
   objects with literal hex values throughout (`color: "#94a3b8"`, `border:
   "1px solid #e2e8f0"`, etc.), not Tailwind classes — confirmed at
   `trading-room-loading` (line ~1028), `trading-room-error` (line ~1039),
   `strategy-list-empty` (line ~574), and the `StrategyList` table header
   (lines ~582-587). A 1:1 Tailwind-class swap like §2.3.2 will not work
   here; the parent has two consistent options and should pick one, not mix
   them file-by-file:
   - convert the inline `style` objects to Tailwind classes (matches
     `TradingDeskLayout.tsx`'s convention), or
   - keep inline `style` but read the CSS variables directly (e.g. `color:
     "hsl(var(--muted-foreground))"` instead of the literal `"#94a3b8"`).
   Either is consistent with "use the existing token system"; silently
   leaving the hex literals in place is the one option that reproduces this
   bug, since hardcoded hex does not respond to the `.dark` class at all.
4. Add `background: "hsl(var(--background))"` (or the Tailwind-class
   equivalent) explicitly to `trading-room-loading`, `trading-room-error`,
   and the root `trading-room-page` container — today none of the three set
   a background, so today they inherit the page's light background; under
   `.dark` they must inherit the dark one instead of staying transparent.

### 2.4 Contract-drift trap: do not use `src/lib/v4/designTokens.ts`

Copy B also has a second, unrelated theming contract that looks superficially
relevant but is not wired to anything live:

```ts
// src/lib/v4/designTokens.ts
// v4 / Pack C §C050–C051 — Dark mode + density tokens.
export type ThemePreference = "system" | "light" | "dark";
export const REQUIRED_THEME_TOKENS = [
  "--bg", "--fg", "--surface",
  "--status-live", "--status-paper", "--risk-high",
] as const;
```

None of `--bg`, `--fg`, `--surface`, `--status-live`, `--status-paper`, or a
`[data-theme="dark"]` selector exist anywhere in `src/index.css` or any other
stylesheet in the repo — confirmed by grep across `src/**/*.css`. The only
consumers of this file are its own v4 test suite
(`src/lib/v4/h2-m-wiring.test.ts`, `src/lib/v4/l-wiring.test.ts`) and
`src/lib/v4/index.ts`/`README.md`. This is a separate, apparently unfinished
`data-theme`-attribute theming contract for a different feature slice (Pack
C, likely Management-console user preferences), not the live `.dark`-class
system that actually backs `tailwind.config.ts`/`index.css`. **If the parent
implementation reaches for `REQUIRED_THEME_TOKENS` or a `data-theme`
attribute, it will produce CSS that resolves to nothing** — the two systems
do not share variable names. Use the `.dark` class + `--background`/`--foreground`/etc.
system in §2.1-2.3; treat `designTokens.ts` as out of scope for this bug.

---

## 3. BFF Query Surface — Reconfirmed, No Drift

Unchanged from the original packet's §4; re-verified this session:

| Function | Route |
|---|---|
| `getTradingRoom` | `GET /bff/agora/trading-room` |
| `getTradingRoomStrategy` | `GET /bff/agora/trading-room/strategies/{id}` |
| `listDecisionEvents` | `GET /bff/agora/trading-room/decision-events` |
| `getDecisionEvent` | `GET /bff/agora/trading-room/decision-events/{id}` |
| `decideOnEvent` | `POST /bff/agora/trading-room/decision-events/{id}/decisions` |

```
$ curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
{"status":"ok","service":"operator-bff","version":"0.2.0", ...}
```

No BFF contract or query-shape gap was found. The bug and its fix remain
entirely UI/theming, as both prior packets already established — this packet
did not find anything to add to the BFF surface beyond re-confirming no
drift.

---

## 4. Operator Journey — Delta From The Original Packet

Original packet §5 steps 1-4 (reproduce the bug, confirm BFF calls succeed)
are unchanged. Add this verification step after the parent's dark-theme fix
lands, specific to the `.dark`-class mechanism in §2:

5. After the fix, inspect the DOM: the ancestor element the parent chose in
   §2.3.1 (e.g. the `AgoraLayoutRoute` wrapper) must carry `class="...dark
   ..."`. If `TradingDeskLayout`'s `trading-desk-shell` renders without a
   `dark`-class ancestor, `bg-background`/`text-foreground`/etc. resolve to
   the **light** CSS variables, and the page will look unchanged even though
   the classes were correctly swapped — this is the most likely way this
   specific fix silently fails.
6. Confirm Management console (`/management/*`) is unaffected — no `dark`
   class should appear on any Management-console ancestor element. Scoping
   the `dark` class to the Agora route subtree, not `<html>`, is what keeps
   this true.
7. Re-run step 6 of the original packet's operator journey (existing
   `strategyId` path, workspace/proposal/grid flows) to confirm the darker
   `bg-card`/`bg-muted`/`border-border` tokens do not clip or wash out the
   grid/widget content that already renders on `TradingRoomWorkspace` state.

---

## 5. Scope Guardrail For The Parent

The parent task `AG-DYNUI-LIVE-DEFAULT-001` acceptance criteria are scoped to
`/agora/trading-room` default-route parity, not full `AG-FE-DYNUI-005`
delivery. §2.2 found that *no* Agora surface in Copy B is dark yet, which
means the parent could be tempted to either (a) silently expand scope to
theme all of `src/agora`, or (b) treat the wider gap as blocking. Neither is
required to close this task:

- fixing `TradingDeskLayout.tsx` (shared shell, used by all three tabs) plus
  `TradingRoomPage.tsx` per §2.3 satisfies this task's own acceptance
  criteria, since the shell wraps every tab and the reported bug is
  specifically the trading-room default route;
- the fact that `StrategyWorkshopPage.tsx` and other Agora pages remain light
  is a separate, larger gap (full `AG-FE-DYNUI-005` re-delivery in Copy B)
  that this task does not need to absorb;
- record that broader gap as a residual/follow-up item in parent closeout
  (see §7) instead of silently expanding this task's diff, or silently
  ignoring it as if it doesn't exist.

---

## 6. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet is added |
| Canonical truth untouched | PASS if no L1 docs, BFF code, or either `execute-plans` copy's runtime code changed by this sidecar |
| Dark-token claim is verifiable | PASS if `src/index.css` in `ajoe734/execute-plans` `dev` shows a `.dark { ... }` block with `--background`, `--foreground`, `--card`, `--sidebar-background`, and re-tuned `--risk-*`/`--status-*`/`--env-*` values |
| "Never activated" claim is verifiable | PASS if `grep -rn 'classList.add("dark")\|className="dark"' src` in that repo returns no matches |
| "No Agora dark classes yet" claim is verifiable | PASS if `grep -rn "bg-slate-900\|bg-zinc-900\|bg-neutral-900\|#111417" src/agora` returns no matches |
| `designTokens.ts` drift claim is verifiable | PASS if `--bg`, `--fg`, `--surface`, `--status-live`, `--status-paper` do not appear in `src/index.css` and `data-theme` does not appear in any `.css` file |
| TradingRoomPage inline-style claim is verifiable | PASS if `trading-room-loading`/`trading-room-error`/`strategy-list-empty` in `src/agora/pages/trading-room/TradingRoomPage.tsx` use inline `style={{ color: "#..." }}`, not Tailwind classes |
| Recommendation stays consistent with prior packets | PASS if this packet still tells the parent to implement in `ajoe734/execute-plans` (Copy B), not `pantheon/execute-plans/` (Copy A) |

---

## 7. Residual Items To Keep Visible

| Item | Owner to absorb | Why it matters |
|---|---|---|
| No Agora surface in Copy B uses the `.dark` token system yet (§2.2, §5) — `AG-FE-DYNUI-005` apparently never landed in the real repo at all | `AG-DYNUI-LIVE-DEFAULT-001` owner / chair | Fixing only `TradingDeskLayout`/`TradingRoomPage` satisfies this task, but the wider gap should be tracked as an explicit follow-up rather than silently left implicit. |
| `src/lib/v4/designTokens.ts`'s `REQUIRED_THEME_TOKENS` contract (`--bg`/`--fg`/`--surface`/`--status-live`/`--status-paper`, `data-theme` attribute) has no corresponding CSS anywhere in the repo | whoever owns the v4/Pack-C theming slice | This looks like an unfinished or abandoned parallel theming contract; worth a dedicated follow-up to either wire it up or retire it, independent of this bug. |
| Prior packet's residual items (Copy A sync/retirement decision, `dev-compatibility-manifest.json` placeholder `runtime_commit`, no live Playwright evidence yet) | unchanged | Still open; not re-verified in this packet since nothing changed since `...FOLLOWUP-2`. |

---

## 8. Verification Performed

| Command | Result |
|---|---|
| `git branch --show-current` / `git status --short` | Sidecar worktree on `task/AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`; only pre-existing untracked file was the generated task brief. |
| `python3 scripts/ai_status.py show AG-DYNUI-LIVE-DEFAULT-001` / `...FOLLOWUP-3` (`PANTHEON_STATUS_ROOT`) | Confirmed parent last activity is still the `2026-07-02T04:53:37Z` "missing agora dist" theory; this sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`. |
| `grep '"task_id": "AG-DYNUI-LIVE-DEFAULT-001"' ai-activity-log.jsonl` (status root) | Confirmed no parent activity after the `04:53:37Z` progress note. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md` | Confirmed this IA contract doc (cited in `TradingDeskLayout.tsx`'s own header comment) defines route/tab structure only, no color/theme requirement — the dark-theme requirement traces to the design pack, not this contract doc. |
| `support/sidecars/AG-FE-DYNUI-005/AG-FE-DYNUI-005-SIDECAR-ACCEPTANCE.md` | Confirmed "dark AGORA global shell ... dense institutional trading-desk tone" is an explicit canonical acceptance criterion for `AG-FE-DYNUI-005`, not an assumption invented by Copy A. |
| `git clone --depth 1 --branch dev https://github.com/ajoe734/execute-plans.git` into a scratch dir | Fresh read of Copy B at unchanged commit `4b0b30c010b4158dded4cb77fdbb13c057f59536`. |
| `cat tailwind.config.ts`, `cat src/index.css` | Confirmed `darkMode: ["class"]` and complete `:root`/`.dark` CSS-variable palettes. |
| `grep -rn 'classList.add("dark")\|className="dark"\|data-theme' src/App.tsx src/main.tsx index.html src/routes/agora.tsx` | No matches — `.dark` class never activated anywhere. |
| `grep -rn "bg-slate-900\|bg-zinc-900\|bg-neutral-900\|bg-gray-900\|#111417" src/agora` | No matches across 41 files — confirms no Agora surface in Copy B is dark yet. |
| `cat src/agora/TradingDeskLayout.tsx` | Read full file; confirmed all light-only Tailwind classes listed in §2.3.2's table. |
| `sed -n` on `src/agora/pages/trading-room/TradingRoomPage.tsx` around `trading-room-loading`/`trading-room-error`/`trading-room-page`/`strategy-list-empty`/`StrategyList` table header | Confirmed inline `style={{ color: "#..." }}` usage, not Tailwind classes, and that none of the three top-level states set an explicit background today. |
| `cat src/lib/v4/designTokens.ts` and `grep -rln "designTokens\|REQUIRED_THEME_TOKENS\|UserUiPreferences" src` | Confirmed `REQUIRED_THEME_TOKENS` names variables absent from `index.css` and is only consumed by its own v4 test files. |
| `grep -rn "data-theme\|--bg:\|--fg:\|--surface:\|--status-live\|--status-paper" src/index.css src/App.css` | No matches — confirms the drift in §2.4. |
| `curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`, `curl .../health` (BFF) | Unchanged from prior packets: commit `4b0b30c0...`, BFF healthy. |

No runtime tests were run; this sidecar changes only a support artifact. It
did not modify either `execute-plans` copy, run a frontend build, or trigger
a new dev FE deployment.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned
beyond the targeted `grep` above, per the task brief's read-order guidance.

---

## 9. Handoff And Review Status

This packet is ready for `Claude` review as an implementation-readiness
update to the `AG-DYNUI-LIVE-DEFAULT-001` parent task. It does not itself
change runtime, BFF, or frontend code in either `execute-plans` copy, and it
does not approve any parent implementation.

Suggested review command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-DYNUI-LIVE-DEFAULT-001/AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3-REVIEW.md \
REVIEW_NOTES_ZH="審查通過：sidecar packet 找到 execute-plans dev 已內建完整但從未啟用的 .dark token 系統，並提供具體的 class/inline-style 替換對照與 designTokens.ts 陷阱提醒||後續：parent owner 需在真實 execute-plans repo 套用 .dark class scoping 並落地修正" \
./scripts/ai-status.sh approve AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
"Sidecar packet approved; support-only implementation-readiness update returned to owner for closeout."
```

If a future reader finds evidence this packet missed (for example, a parent
commit landing in `ajoe734/execute-plans` after this packet was written),
open a narrow follow-up packet with that exact correction instead of
changing canonical or parent runtime files from a sidecar.

Prepared by `Claude2` for the `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
support slice.
