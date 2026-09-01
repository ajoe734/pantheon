"""Single source of truth for which readiness fields may block a release.

`GET /bff/auth/readiness` returns two very different kinds of signal in one
payload, and the difference is not obvious from the field names:

* Release-blocking: local-authority facts about *this build and this session*.
  If one of these is wrong, the release itself is wrong and must not ship.
* Advisory: observability about things this build does not own — most notably
  assistant-provider health, which depends on an external LLM credential that
  rotates on its own schedule. `_bff_auth_readiness` deliberately computes
  `ready`/`authReady` without it, because "a provider outage or probe failure
  must never flip a validly authenticated strict session to not-ready".

Treating an advisory field as blocking is not a harmless over-caution: it makes
every release conditional on an unrelated external credential, and it recurs on
every rotation. That happened — a deploy gate asserted `providerReady is True`
and auto-rolled-back four healthy releases (OPGAP-DEPLOY-PROVIDER-GATE-20260901).

Deploy gates must assert only fields declared blocking here. The pairing is
enforced by tests on both sides: this module's own test proves every field the
endpoint emits is classified, and the deploy-script contract test proves the
gate asserts nothing outside RELEASE_BLOCKING_FIELDS.
"""
from __future__ import annotations

from typing import FrozenSet


# Dotted paths, relative to the readiness response's "data" object.
RELEASE_BLOCKING_FIELDS: FrozenSet[str] = frozenset(
    {
        # The deployed artifact is the one that was admitted.
        "sourceCommitSha",
        # Local-authority auth truth for this build.
        "ready",
        "authReady",
        "auth.mode",
        "auth.stub",
        "auth.strict",
        "auth.sessionKind",
        "auth.sessionReady",
        "auth.operatorRoleReady",
        "auth.interactionCapabilityReady",
        "auth.verifierReady",
        "auth.verifier",
    }
)

# Emitted for humans and dashboards. Never a release gate.
ADVISORY_FIELDS: FrozenSet[str] = frozenset(
    {
        # Assistant provider health: external credential, rotates independently.
        "providerReady",
        "provider",
        # Who the probing session resolved as; useful in evidence, not a gate.
        "identity",
        # Static posture describing what this surface is allowed to command.
        "authority",
    }
)


def classified_fields() -> FrozenSet[str]:
    return RELEASE_BLOCKING_FIELDS | ADVISORY_FIELDS


def is_release_blocking(field_path: str) -> bool:
    return field_path in RELEASE_BLOCKING_FIELDS
