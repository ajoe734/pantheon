# BFF Response Envelope

Status: draft-canonical
Last updated: 2026-04-22
Source of truth inputs:
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Followup_Questions.md`
- `docs/reviews/Pantheon_Response_to_Architecture_Blockers_Decision_Package.md`
Tier: L1 Platform Architecture & Policy
Scope: minimum shared BFF list/detail envelope and pagination wrapper
Conflict rule: module contracts may extend the envelope with domain fields, but they should not remove or redefine the shared minimum fields documented here without an explicit narrower decision

## Purpose

This document defines the minimum shared response envelope for Pantheon BFF
surfaces. It intentionally defines only the common shell. Domain contracts
still own their domain-specific body fields.

## Detail response minimum

Every detail response should expose, when applicable:

- `object_ref`
  - `type`
  - `id`
- `status`
- `lifecycle_state`
- object-shaped `allowedActions`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces`
- `links`
- one domain block or domain-shaped primary fields

Important:

- `object_ref` is a common operator-facing wrapper. It does not replace the
  domain object's canonical identity fields.
- Generic `id` is not mandatory if the domain already uses a stronger primary
  key such as `decision_id`, `request_id`, `session_id`, or `artifact_id`.
- Generic `title` is not mandatory. Optional display metadata may live under
  `display`.
- `allowedActions` must remain object-shaped.

Example:

```json
{
  "object_ref": {
    "type": "MutationReview",
    "id": "evo_dec_88f3a2c1"
  },
  "display": {
    "title": "Review mutation for Strategy X",
    "subtitle": "candidate deployment"
  },
  "status": "reviewed",
  "lifecycle_state": "reviewed",
  "allowedActions": {
    "canApproveMutation": true,
    "canRejectMutation": true
  },
  "meta": {
    "snapshot_at": "2026-04-22T00:00:00Z",
    "staleness": {
      "status": "fresh",
      "as_of": "2026-04-22T00:00:00Z"
    },
    "surfaces": {
      "mutation_review": {
        "state": "ok"
      }
    }
  },
  "links": {},
  "data": {
    "decision_id": "evo_dec_88f3a2c1"
  }
}
```

## List response minimum

Every list response should expose:

- `items`
- `page_info.next_page_token`
- `page_info.page_size`
- `page_info.has_more`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces`

Optional shared additions:

- `page_info.total`
- module-local filter metadata
- module-local sort metadata
- module-local aggregate counts

Important:

- Pantheon list routes use cursor-based pagination.
- Canonical output field remains `page_info.next_page_token`.
- Canonical output must not switch to `next_cursor` without an explicit
  migration policy.

Example:

```json
{
  "items": [],
  "page_info": {
    "next_page_token": null,
    "page_size": 50,
    "has_more": false
  },
  "meta": {
    "snapshot_at": "2026-04-22T00:00:00Z",
    "staleness": {
      "status": "fresh",
      "as_of": "2026-04-22T00:00:00Z"
    },
    "surfaces": {
      "ticket_list": {
        "state": "ok"
      }
    }
  }
}
```

## Freshness and degradation rule

- Freshness must be represented through `meta.staleness`.
- Surface availability or degradation must be represented through
  `meta.surfaces.<surface>.state`.
- New contracts must not encode `stale` as the primary surface state.
- `partial` is allowed only for non-authoritative read surfaces and follows
  `docs/conventions/DEGRADATION_DICTIONARY.md`.

## Pagination alias policy

BFF adapters may accept internal or legacy aliases such as:

- `next_cursor`
- `cursor`
- `nextToken`

But canonical BFF responses must output:

```text
page_info.next_page_token
```

## Non-goals

This document does not force:

- a generic `id` field on every detail object
- a generic `title` field on every detail object
- one giant cross-domain lifecycle enum
- one giant cross-domain list row shape

Those belong to module contracts and domain models.
