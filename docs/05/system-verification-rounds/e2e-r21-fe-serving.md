# E2E-R21 — Frontend static-serving (the blank-console blind spot)

**Round:** E2E-R21 (triggered by the user observing an empty management console)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r21-fe-serving

## Why this round exists

The user reported the management console showed **no content**, despite R1–R20
reporting the backend "done". Investigation found the real cause — and a genuine
blind spot in the whole campaign:

**All 20 prior rounds verified the BFF/API layer by supplying a bearer token
directly (`curl -H "Authorization: Bearer …"`). None exercised the actual
browser → FE → BFF render path. "The API returns data with a token" does NOT
imply "the console shows content."**

## Root cause of the blank console

The dev FE (`/var/www/pantheon-dev-fe`, served by Caddy with
`try_files {path} /index.html`) had a **mismatched deploy**: `index.html`
referenced `/assets/index-qpmLpS0Z.js`, but that JS bundle was **absent** from the
assets directory (the CSS was present). Caddy's SPA catch-all returned
`index.html` (text/html, 1289 bytes) for the JS request → the browser received
HTML where it expected a JS module → the app never booted → blank `<div id=root>`.
The backend was entirely healthy; the FE was broken at the static-serving layer.

## What changed during the session

A fresh FE deploy landed at **2026-06-15 12:45:24Z** (commit `4ede98f7`, branch
`dev`) that replaced the directory with a consistent `index.html` +
`/assets/index-CcSTxh-W.js` (4.27 MB). The JS now serves as `text/javascript`.

## Verification (live, post-deploy)

`scripts/verify_e2e_fe_serving.py` fetches the FE index, extracts every
referenced `/assets/*.js|css`, and asserts each is served with the correct MIME
(not the SPA HTML fallback):

```
/assets/index-CcSTxh-W.js : 200 text/javascript 4271278 bytes  (not html fallback)
/assets/index-B7djYzZC.css: 200 text/css         93834 bytes
OK: every index-referenced asset is served with the correct type (app can boot)
```

End-to-end auth/data path also confirmed with the FE's own baked token
(`VITE_BFF_DEV_BEARER_TOKEN = pantheon-dev-browser:operator:mfa:…`, mode=live):
`/bff/me` 200, `/bff/runtimes` 200 (16), `/bff/personas` 200 (12). So the console
will now render the populated surfaces; genuinely-empty surfaces (loop-runs,
strategies, artifacts, approvals) correctly show empty — the documented upstream
rescue-placeholder gaps (R1/R3/R5/R9).

## Disposition

- **Shipped (code/CI):** `verify_e2e_fe_serving.py` + unit test (gated by the
  R20 `e2e-verifier-suite` glob), and an `FE_BASE`-gated step in
  `run_e2e_verifiers.sh`. Had this existed, it would have FAILed before 12:45 and
  caught the blank console directly.
- **User action:** hard-refresh the console (Cmd/Ctrl-Shift-R) — the browser may
  still cache the old broken index.html that pointed at the missing bundle.

## Lesson

Add FE-serving (and ideally a headless render) checks to the e2e suite — an
all-green API campaign can still sit behind a blank UI.
