# Round 27 — Request body-size limits (DoS surface)

**Date:** 2026-06-15
**Depth/breadth step:** A resource-exhaustion round. Does the BFF/edge bound
request body size, or accept arbitrarily large payloads into memory?

## Hypotheses

- H1: oversized request bodies are rejected before exhausting memory.

## Method

1. Grep BFF code + Caddy templates for a body-size limit.
2. Measured probe: send a 2MB body to a POST endpoint; observe accept/reject.
3. Confirm no endpoint legitimately needs large bodies (multipart uploads).
4. If unbounded, add an edge limit generous enough for all legit JSON bodies.

## Pass criteria

- H1: an edge body-size limit exists, validated with `caddy validate`, and
  doesn't break any legitimate request.
