# Optional Channel Services

The web channel is an optional HTTP adapter in front of the control-plane router. It exposes the repository-standard health surface:

- `GET /healthz`
- `GET /livez`
- `GET /readyz`
- `GET /health` as the legacy alias

`/readyz` probes the router dependency and reports it as `ok`, `degraded`, or `unavailable` without crashing the channel process.

Telegram and Discord are SDK-driven bot clients. They do not expose an HTTP process, so the FastAPI health contract does not apply until they are wrapped in a Pantheon-owned HTTP supervisor or sidecar.

These channel services remain out of the default compose stack. They should only be activated through an explicit optional channel profile or an operator-run process.
