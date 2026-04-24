Build the `RW-01-research-ticket` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-01-research-ticket-bff-gap.yaml` using `.coordination/requests/RW-01-research-ticket-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `research-ticket`.
Workbench: `research-workbench`.
Screen ID: `screen-research-ticket`.
Allowed endpoints:
- POST /api/v1/research/tickets
- GET /api/v1/research/tickets
- GET /api/v1/research/tickets/{ticket_id}
- PATCH /api/v1/research/tickets/{ticket_id}
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- do not start production UI until Pantheon confirms the RW-01 ticket routes are live
- build the ticket composer from the published create contract only
- build the ticket list and detail pages from the RW-01 BFF routes only
- render lifecycle history from lifecycle_history[] as supplied by the BFF
- render edit/close/archive CTAs only when the respective allowedActions signal is true
- render linked experiments and artifacts as BFF-supplied read-only refs
- emit a bff-gap handoff if any required field is absent
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-01-research-ticket-ui-done.yaml` using `.coordination/requests/RW-01-research-ticket-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/RW-01-research-ticket.md
- docs/pantheon-handoffs/RW-01-research-ticket/FRONTEND_CHANGE_SPEC.md
- docs/bff/RW-01-research-ticket.md
- docs/pantheon-handoffs/RW-01-research-ticket
- docs/examples/RW-01-research-ticket.json
