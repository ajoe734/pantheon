# AG-DYNUI-LIVE-DEFAULT-001 BFF and Frontend Handoff Packet — Follow-up 2

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-DEFAULT-001` |
| Parent title | Fix live Agora Trading Room default route visual parity |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar task | `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Prior sidecar | `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF` (merged via PR #2746, `done` at 2026-07-02T04:59:42Z) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-02` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, Caddy config, or `execute-plans` frontend code in
either repo copy discussed below. Parent ownership and review decide how to
act on this packet.

---

## 1. Why This Follow-up Exists

The prior sidecar packet (`AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF`)
diagnosed the white Trading Desk skeleton as a **frontend-repo composition
gap**: the dark AGORA theme landed only in the Pantheon-tracked
`execute-plans/` mirror, never in the real `ajoe734/execute-plans` repo the
live dev FE is built from.

After that packet was approved and merged (2026-07-02T04:57-04:59Z), the
parent task's own `next` field recorded a **different** working theory at
`2026-07-02T04:53:37Z` (overlapping in time with the prior packet's review):

> "Root cause found: execute-plans/src/agora TradingDeskLayout+TradingRoomPage
> already implement the correct dark AGORA UI. Live dev FE at
> pantheon-lupin-dev-fe never had the agora dist deployed at all -- only
> dist/management is installed at /var/www/pantheon-dev-fe, and
> deploy/caddy/dev.Caddyfile.tmpl has a single try_files fallback to
> /index.html (the management build), so /agora/trading-room silently serves
> the Management Console SPA instead of a 404 or the agora bundle. Fixing
> Caddyfile routing + building/deploying missing agora dist + live Playwright
> evidence."

**These two theories are mutually exclusive on the facts.** The first says the
deployed app's own code is light-themed. The second says the deployed app's
code is already correct (dark) but a separate `dist/agora` bundle was never
installed, so Caddy's single-root `try_files` fallback serves the management
bundle for `/agora/*` paths instead.

This follow-up packet exists to re-verify both theories against current
evidence before the parent owner spends implementation time on the wrong one,
since a Caddyfile/build-pipeline fix and a frontend-repo-content fix are very
different scopes of work.

---

## 2. Re-verified Finding: The "Missing Agora Dist" Theory Does Not Match The Deployed Repo's Build Architecture

The "separate `dist/agora` bundle" idea is real — but it describes **Copy A**
(the Pantheon-tracked mirror at `execute-plans/` inside this `ajoe734/pantheon`
repo), not **Copy B** (the real, deployed `ajoe734/execute-plans` repo).

### 2.1 Copy A does have a management/agora build split

`execute-plans/package.json` (this repo, i.e. the mirror) at commit time:

```json
"dev:agora": "vite --config vite.agora.config.ts",
"dev:management": "vite --config vite.management.config.ts",
"dev": "npm run dev:agora",
"build:agora": "vite build --config vite.agora.config.ts",
"build:management": "vite build --config vite.management.config.ts",
"build": "npm run build:agora && npm run build:management",
```

This is exactly the `dist/agora` / `dist/management` split referenced in
several `AG-FE-DB-*` / `AG-FE-ID-001` sidecar packets (e.g.
`support/sidecars/AG-FE-DB-003/AG-FE-DB-003-SIDECAR-REVIEW.md`,
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF.md`), which
all run `npm --prefix execute-plans run build:agora` — i.e. against Copy A.

### 2.2 Copy B (the real, deployed repo) has no such split

`/home/lupin/code/execute-plans/package.json` scripts, re-verified this
session:

```json
"dev": "vite",
"build": "vite build",
"build:dev": "vite build --mode development",
"lint": "eslint .",
"preview": "vite preview",
```

No `build:agora`, no `build:management`. `vite.config.ts` in that repo is a
single `defineConfig` with one dev server and one build target; there is only
one `index.html` at the repo root (no `agora.html` / `management.html`). This
is the same single-SPA shape the prior packet already documented (§3.2 of the
FOLLOWUP packet) — `src/App.tsx` with `react-router-dom` routing both
`/agora/*` and `/management/*` inside one bundle.

### 2.3 The Caddy config matches a single-bundle deployment, not a broken split

`deploy/caddy/dev.Caddyfile.tmpl` (re-read this session):

```
__FE_HOST__ {
	root * __FE_ROOT__
	...
	try_files {path} /index.html
	file_server
}
```

One `root`, one `try_files` fallback, `FE_ROOT` defaults to
`/var/www/pantheon-dev-fe` (`deploy/caddy/sync-caddy.sh`). There is no
per-path branching (`handle /agora/* { root ... dist/agora }` vs `handle
/management/* { root ... dist/management }`) anywhere in the template. This is
the correct, standard Caddy config for serving **one** client-side-routed SPA
build — which is what Copy B's build actually produces. It is not evidence of
a routing bug; it is evidence that the deploy target expects exactly the
single-bundle shape Copy B builds.

### 2.4 No drift since the prior packet

Re-run this session, unchanged from the prior packet's findings:

```
$ curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
{
  "app": "execute-plans", "commit": "4b0b30c010b4158dded4cb77fdbb13c057f59536",
  "sourceBranch": "dev", "deployedAt": "20260702T040955Z", ...
}
```

`git fetch --depth 1 origin dev` against `ajoe734/execute-plans` from
`/home/lupin/code/execute-plans` reports `origin/dev` still at
`4b0b30c010b4158dded4cb77fdbb13c057f59536` (forced-update fetch, same SHA —
no new commits landed). `git show origin/dev:src/agora/TradingDeskLayout.tsx`
still contains `border-slate-200 bg-white`, `bg-slate-50`, no dark palette
constant. Nothing about the deployed commit or its content has changed since
the prior packet's evidence was captured at `04:46-04:53Z`.

### 2.5 Conclusion

Given §2.1-2.4, the evidence continues to support the **original** diagnosis
from `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF`: the live dev FE genuinely
executes Copy B's own (light-themed) `TradingDeskLayout.tsx` /
`TradingRoomPage.tsx` for `/agora/trading-room`, in one unified bundle. There
is no separate `dist/agora` artifact missing from `/var/www/pantheon-dev-fe`
in Copy B's current build model, so there is nothing for a Caddyfile change or
a "build the missing agora dist" step to fix in that repo as it stands today.

**If the parent owner intends to introduce a Copy-A-style split build
(`build:agora` + `build:management`) into the real `ajoe734/execute-plans`
repo**, that is a legitimate but much larger architecture change — a new
Vite multi-entry config, a new Caddy per-path `handle` block, and a new deploy
step — and should be scoped and reviewed as such, not assumed as the fix for
this bug. Nothing in the current evidence requires that change to fix the
reported visual-parity bug; recoloring Copy B's existing single-bundle
`TradingDeskLayout.tsx` / `TradingRoomPage.tsx` (per the original packet's
§6 rules) is sufficient on the facts gathered so far.

---

## 3. What The Parent Owner Should Do Before Implementing

1. Re-confirm which theory this packet's §2 evidence should update: either
   accept that the fix is a Copy B content/theme change (original packet), or
   present new evidence this packet missed (e.g. a build step that installs
   only a `management` subset from Copy B that this packet did not find) that
   would revive the missing-dist theory.
2. Do not start Caddyfile or deploy-pipeline changes on the missing-dist theory
   without first finding the specific evidence for it (e.g. an actual
   `dist/management`-only rsync/scp step in the `ajoe734/execute-plans` CI
   workflow, or VM-side proof that `/var/www/pantheon-dev-fe` lacks agora
   route assets present in the built bundle). This packet checked the Pantheon
   repo's own deploy/Caddy templates and Copy B's local build scripts and
   found no such split; it did not have access to run the real repo's own
   GitHub Actions deploy workflow logs.
3. If §2's conclusion is accepted, resume the original packet's plan
   (§4-§6 of `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF.md`): implement in
   `ajoe734/execute-plans` (`/home/lupin/code/execute-plans` or a clean task
   worktree), preserve the `react-router-dom` shape, theme via the existing
   Tailwind/`cn()` convention, and record the new `deployment.json` commit as
   closeout evidence.

---

## 4. Scope

In scope for this packet:

1. Reconcile the parent task's newer working theory against the previously
   delivered BFF/frontend handoff packet.
2. Re-verify live deployment state has not drifted since the prior packet.
3. Confirm whether the real deployed repo's build architecture supports a
   "missing agora dist" explanation.

Non-goals (unchanged from the prior packet):

- no edits to `pantheon/execute-plans/src/agora/*` (Pantheon-tracked mirror);
- no edits to `ajoe734/execute-plans` (the real frontend repo);
- no edits to `deploy/caddy/*` or BFF/backend Agora routes;
- no new frontend build, deploy, or Playwright run from this sidecar;
- no approval of the parent implementation.

---

## 5. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this packet is added |
| Canonical truth untouched | PASS if no L1 docs, Caddy config, BFF code, or either `execute-plans` copy's runtime code changed by this sidecar |
| Build-split claim for Copy A is verifiable | PASS if `execute-plans/package.json` (Pantheon repo) shows `build:agora` / `build:management` scripts and `vite.agora.config.ts` / `vite.management.config.ts` exist |
| No build-split in Copy B is verifiable | PASS if `/home/lupin/code/execute-plans/package.json` shows only a single `build` script (`vite build`) and no `agora.html`/`management.html` at repo root |
| Caddy template claim is verifiable | PASS if `deploy/caddy/dev.Caddyfile.tmpl` shows one `root` + one `try_files {path} /index.html` block with no per-path `handle` branching |
| No drift since prior packet | PASS if `GET https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` still reports commit `4b0b30c010b4158dded4cb77fdbb13c057f59536` and that commit's `TradingDeskLayout.tsx` is still light-themed |

---

## 6. Verification Performed

| Command | Result |
|---|---|
| `git branch --show-current` / `git status --short` | Sidecar worktree on `task/AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; only pre-existing untracked file was the generated task brief. |
| `grep -n "AG-DYNUI-LIVE-DEFAULT-001" /home/lupin/code/pantheon/ai-status.json` | Read the current parent task `next` field recording the "missing agora dist" theory at `2026-07-02T04:53:37Z`. |
| `grep -n "AG-DYNUI-LIVE-DEFAULT-001" /home/lupin/code/pantheon/ai-activity-log.jsonl` | Confirmed prior sidecar (`...SIDECAR-BFF-HANDOFF`) lifecycle: `start` -> `handoff` (PR #2746) -> `review_approved` -> `done`, and this follow-up's `sidecar_task_created` dispatch reason (`supervisor-underutilization`). |
| `cat deploy/caddy/dev.Caddyfile.tmpl` | Confirmed single `root` / single `try_files {path} /index.html` fallback, no per-path `handle` branching. |
| `cat execute-plans/package.json` (Pantheon-repo mirror, Copy A) | Confirmed `build:agora` / `build:management` split scripts exist only here. |
| `cd /home/lupin/code/execute-plans && cat package.json`, `cat vite.config.ts`, `find . -maxdepth 1 -iname "*.html"` | Confirmed the real repo (Copy B) has a single `build` script, a single `vite.config.ts`, and only one `index.html` — no agora/management split. |
| `curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Unchanged from prior packet: commit `4b0b30c010b4158dded4cb77fdbb13c057f59536`, `deployedAt: 20260702T040955Z`. |
| `curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | BFF healthy (`{"status":"ok", ...}`). |
| `cd /home/lupin/code/execute-plans && git fetch --depth 1 origin dev` | `origin/dev` still `4b0b30c010b4158dded4cb77fdbb13c057f59536` (forced-update fetch, same SHA — no new commits). |
| `git show origin/dev:src/agora/TradingDeskLayout.tsx` | Still light-themed (`border-slate-200 bg-white`, `bg-slate-50`, no dark palette constant); re-confirms no drift. |
| `grep -rn "dist/management\|dist/agora\|/var/www/pantheon-dev-fe"` across repo (excluding `node_modules`) | Located every reference; all `dist/agora`/`dist/management` mentions trace back to Copy A (`execute-plans/` mirror) task packets, none to a Copy B deploy path. |

No runtime tests were run; this sidecar changes only a support artifact. It
did not modify either `execute-plans` copy, `deploy/caddy/*`, run a frontend
build, or trigger a new dev FE deployment.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned beyond
the targeted `grep` above, per the task brief's read-order guidance.

---

## 7. Handoff And Review Status

This packet is ready for `Claude` review as an update to the
`AG-DYNUI-LIVE-DEFAULT-001` parent implementation guidance. It flags a
conflict between the parent's two working theories and recommends which one
the current evidence supports; it does not itself change runtime, BFF, Caddy,
or frontend code in either `execute-plans` copy.

If a future reader finds evidence this packet missed (for example, a real
`ajoe734/execute-plans` CI step that installs only a `management` subset), open
a narrow follow-up packet with that exact correction instead of changing
canonical or parent runtime files from a sidecar.

Prepared by `Claude2` for the `AG-DYNUI-LIVE-DEFAULT-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
support slice.
