# KW-02 Research Notes — UI Decisions

- **Markdown rendering:** body shown as preformatted text. Adding a markdown renderer (e.g. `react-markdown`) can be a follow-up if the design team wants rich rendering.
- **Tag filter:** comma-separated string passed through to backend `tags` query param exactly as documented; no client-side parsing.
- **Pagination:** keyset cursor only via `next_page_token`. No page-number UI.
- **Untitled notes:** show italicized "Untitled note" placeholder; sort/order remains BFF-controlled.
- **Free-standing attachment:** `attachment_ref` input is hidden when type=free_standing and submitted as `null`.
- **Surface banners:** amber for `degraded`, destructive for `unavailable`; both non-dismissable.
- **Owner field:** never collected from user; server-assigned per contract.
