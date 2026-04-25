# KW-02 Research Notes — QA Status

- **Type check:** `npx tsc --noEmit` → 0 errors.
- **Routes registered:** `/knowledge/notes`, `/knowledge/notes/new`, `/knowledge/notes/:note_id`.
- **BFF client:** new methods exported from `researchNotesBffApi`; default export updated.
- **No raw fetch:** verified via search — only `bffClient` calls used.
- **Degradation banners:** rendered for all 4 surface keys.
- **Live BFF integration:** pages will hit live route once `VITE_USE_MOCK_BFF=false`. Until then, mock fallback is intentionally absent (live-route family per contract).

## Manual test plan
- Open `/knowledge/notes` → list renders with filter bar; pagination works for `has_more=true`.
- Apply `attachment_type=research_ticket` filter → query param updates and list refreshes.
- Click row → detail page loads with evidence + memory panels.
- Click "New note" → create form; submit with empty body shows local validation; submit valid body navigates to returned `route_href`.
- Force degraded surface state in BFF → amber banner appears.
