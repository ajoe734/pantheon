# Pantheon Lovable Trigger

This tool sends a prompt into an existing Lovable project chat by driving the
Lovable web app in a persistent Playwright Chromium session.

Why this exists:

- Lovable's official `Build with URL` API creates new apps from
  `https://lovable.dev/?autosubmit=true#prompt=...`.
- The public docs do not describe an official API for posting a prompt into an
  already-existing `/projects/<id>` chat.
- GitHub sync and issue bridges are useful pickup lanes, but they do not prove
  that Lovable has actually started implementation.

So this tool fills the gap locally:

1. bootstrap or reuse an authenticated Lovable browser profile
2. open the existing project URL
3. extract the real prompt from a repo-local prompt file
4. paste and submit it into the Lovable composer

## Requirements

- Node.js and npm
- Playwright Chromium binary available locally
- A Lovable account with access to the project

The default Chromium path is discovered from Playwright's cache under:

- `~/.cache/ms-playwright/chromium-*/chrome-linux/chrome`

## Install

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
npm install
```

## One-time login bootstrap

Run this once from a real desktop session with a visible display:

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
node send_prompt.mjs bootstrap \
  --repo /home/lupin/code/front-ai-trading-system
```

The bootstrap command opens the Lovable project page in a persistent browser
profile and waits until the session is authenticated for that project.

`send` and `batch` now prefer the same persistent headed browser profile when a
desktop session is available. On Linux machines without `DISPLAY`, the tool
falls back to headless mode automatically, but the initial login bootstrap must
still be completed on a machine/session where you can interact with the
browser.

After bootstrap, the profile directory contains a reusable
`storage-state.json`. You can copy that one file to a headless VM and use it
there without a visible browser.

## Headless VM flow

On any machine where you can log into Lovable:

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
node send_prompt.mjs bootstrap \
  --repo /home/lupin/code/front-ai-trading-system \
  --profile-dir /tmp/lovable-auth-profile
```

Then copy this file to the VM:

```bash
/tmp/lovable-auth-profile/storage-state.json
```

On the headless VM:

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
LOVABLE_STORAGE_STATE=/path/to/storage-state.json ./send_current_front_requests.sh
```

You can also send one prompt directly:

```bash
node send_prompt.mjs send \
  --repo /home/lupin/code/front-ai-trading-system \
  --storage-state /path/to/storage-state.json \
  --prompt-file /home/lupin/code/front-ai-trading-system/docs/lovable/2026-04-24-route-live-activation-prompt.md
```

If Lovable expires the session, repeat bootstrap on a machine with a visible
browser and replace the copied `storage-state.json`.

### Windows desktop shortcut

If your desktop machine is Windows and does not have the repo checked out, you
can use the standalone auth-only script:

- [bootstrap_auth_only.mjs](/home/lupin/code/pantheon/tools/lovable-trigger/bootstrap_auth_only.mjs:1)

PowerShell steps:

```powershell
mkdir $HOME\lovable-auth
cd $HOME\lovable-auth
npm init -y
npm install playwright
npx playwright install chromium
```

Copy `bootstrap_auth_only.mjs` into that folder, then run:

```powershell
node .\bootstrap_auth_only.mjs
```

After login succeeds, copy `storage-state.json` from that folder back to the VM.

## Send one prompt

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
node send_prompt.mjs send \
  --repo /home/lupin/code/front-ai-trading-system \
  --prompt-file /home/lupin/code/front-ai-trading-system/docs/lovable/2026-04-24-route-live-activation-prompt.md
```

The tool extracts the first fenced code block if present; otherwise it sends the
whole file.

During submission it also watches the project chat `POST` request and reloads
the page to confirm that the prompt survived as a durable message.

## Batch send multiple prompt files

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
node send_prompt.mjs batch \
  --repo /home/lupin/code/front-ai-trading-system \
  --cooldown-ms 45000 \
  --prompt-file /home/lupin/code/front-ai-trading-system/docs/lovable/2026-04-24-route-live-activation-prompt.md \
  --prompt-file /home/lupin/code/front-ai-trading-system/docs/lovable/2026-04-24-reopened-evolution-consultation-realignment-prompt.md \
  --prompt-file /home/lupin/code/front-ai-trading-system/docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md
```

Batch mode reuses one persistent browser context for the whole run and waits
between prompts instead of opening a brand-new session for every message.

For the current Pantheon front-end request set, you can use the shortcut:

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
./send_current_front_requests.sh
```

Or on a headless VM with imported auth:

```bash
cd /home/lupin/code/pantheon/tools/lovable-trigger
LOVABLE_STORAGE_STATE=/path/to/storage-state.json ./send_current_front_requests.sh
```

The wrapper also accepts `LOVABLE_COOLDOWN_MS` if you want to lengthen or
shorten the pause between prompts.

## Notes

- The tool intentionally fails if the persistent session is not authenticated.
- If Lovable shows a verification gate that does not clear on its own, the tool
  stops and keeps the screenshot/evidence instead of pretending the submission
  succeeded.
- It does not invent project ids. By default it discovers the existing Lovable
  project URL from the target repo's `README.md`.
- Screenshots from each send attempt are stored under `screenshots/`.
