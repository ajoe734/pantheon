from __future__ import annotations

import importlib.util
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest


MODULE_PATH = Path(__file__).with_name("product_closeout_verdict.py")
SPEC = importlib.util.spec_from_file_location("l12_product_closeout_governance", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
pcv = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pcv
SPEC.loader.exec_module(pcv)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def authority(tmp_path: Path) -> dict:
    private_key = pcv.generate_private_key()
    policy = pcv.VerdictPolicy(
        policy_version="product-closeout-v1",
        public_keys={"human-ops-key-1": pcv.public_key_bytes(private_key)},
        max_verdict_age_seconds=120,
        max_ttl_seconds=300,
    )
    ledger = pcv.ProtectedVerdictLedger(tmp_path / "protected" / "verdicts.jsonl")
    clock = MutableClock(datetime(2026, 7, 26, 12, 0, tzinfo=UTC))
    service = pcv.ProductCloseoutVerdictService(
        ledger=ledger,
        policy=policy,
        private_key=private_key,
        key_id="human-ops-key-1",
        clock=clock,
    )
    verifier = pcv.ProductCloseoutVerdictService(
        ledger=ledger,
        policy=policy,
        clock=clock,
    )
    binding = pcv.CloseoutBinding(
        program_id="pantheon-twelve-loop-gap-2026-07-26",
        catalog_sha256="a" * 64,
        task_id="L12-CLOSE-001",
        closeout_manifest_sha256="b" * 64,
        target_environment="pantheon-lupin-dev",
        frontend_sha="c" * 40,
        bff_sha="d" * 40,
    )
    actor = pcv.HumanOpsIdentity(
        actor_id="ops-user-001",
        actor_role="ops",
        authenticated=True,
        mfa_verified=True,
    )
    return {
        "private_key": private_key,
        "policy": policy,
        "ledger": ledger,
        "clock": clock,
        "service": service,
        "verifier": verifier,
        "binding": binding,
        "actor": actor,
    }


def _issue(authority: dict, **overrides: object) -> dict:
    defaults = {
        "decision": "approved",
        "ttl_seconds": 180,
        "verdict_id": "pclose-test-001",
        "nonce": "nonce-product-closeout-001",
    }
    defaults.update(overrides)
    return authority["service"].issue(
        authority["binding"],
        authority["actor"],
        **defaults,
    )


def test_schema_and_runtime_contract_require_exact_catalog_binding_fields() -> None:
    schema = json.loads(
        Path(__file__).with_name("product_closeout_verdict.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert tuple(schema["required"]) == pcv.VERDICT_BINDING_FIELDS
    assert schema["additionalProperties"] is False


def test_authorized_human_ops_issues_signed_exactly_bound_verdict(authority: dict) -> None:
    verdict = _issue(authority)

    assert tuple(verdict) == pcv.VERDICT_BINDING_FIELDS
    assert verdict["actor_id"] == "ops-user-001"
    assert verdict["actor_role"] == "ops"
    assert verdict["signature_algorithm"] == "ed25519"
    assert verdict["decision"] == "approved"
    assert verdict["verifier_capability_sha256"] == authority[
        "policy"
    ].capability_sha256("human-ops-key-1")
    assert authority["verifier"].verify(
        verdict["verdict_id"],
        expected_binding=authority["binding"],
    ) == verdict

    schema = json.loads(
        Path(__file__).with_name("product_closeout_verdict.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(verdict, schema, format_checker=jsonschema.FormatChecker())


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (
            pcv.HumanOpsIdentity("ops-1", "ops", False, True),
            "authenticated",
        ),
        (
            pcv.HumanOpsIdentity("ops-1", "ops", True, False),
            "MFA",
        ),
        (
            pcv.HumanOpsIdentity("ops-1", "viewer", True, True),
            "not Human/Ops-authorized",
        ),
        (
            pcv.HumanOpsIdentity(
                "service-1",
                "ops",
                True,
                True,
                principal_type="service",
            ),
            "cannot issue",
        ),
        (
            pcv.HumanOpsIdentity("Codex", "ops", True, True),
            "fleet actors",
        ),
        (
            pcv.HumanOpsIdentity("codex", "ops", True, True),
            "fleet actors",
        ),
        (
            pcv.HumanOpsIdentity("codex1_4", "ops", True, True),
            "fleet actors",
        ),
        (
            pcv.HumanOpsIdentity("ops-1", "ops", "true", True),
            "authenticated",
        ),
        (
            pcv.HumanOpsIdentity("ops-1", "ops", True, "true"),
            "MFA",
        ),
    ],
)
def test_unauthorized_and_fleet_issuers_fail_closed(
    authority: dict,
    identity: object,
    message: str,
) -> None:
    with pytest.raises(pcv.VerdictAuthorizationError, match=message):
        authority["service"].issue(
            authority["binding"],
            identity,
            decision="approved",
        )
    assert authority["ledger"].records() == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("program_id", "different-program"),
        ("catalog_sha256", "e" * 64),
        ("task_id", "L12-OTHER-001"),
        ("closeout_manifest_sha256", "f" * 64),
        ("target_environment", "wrong-environment"),
        ("frontend_sha", "1" * 40),
        ("bff_sha", "2" * 40),
    ],
)
def test_every_expected_binding_mismatch_is_rejected(
    authority: dict,
    field: str,
    replacement: str,
) -> None:
    verdict = _issue(authority)
    values = authority["binding"].to_dict()
    values[field] = replacement
    wrong = pcv.CloseoutBinding.from_mapping(values)

    with pytest.raises(pcv.VerdictIntegrityError, match=field):
        authority["verifier"].verify(
            verdict["verdict_id"],
            expected_binding=wrong,
        )


def test_rejected_decision_cannot_close(authority: dict) -> None:
    verdict = _issue(authority, decision="rejected")
    with pytest.raises(pcv.VerdictIntegrityError, match="not approved"):
        authority["verifier"].verify(
            verdict["verdict_id"],
            expected_binding=authority["binding"],
        )


def test_revocation_is_checked_from_current_protected_ledger(authority: dict) -> None:
    verdict = _issue(authority)
    authority["service"].revoke(
        verdict["verdict_id"],
        authority["actor"],
        reason="deployment identity no longer current",
    )

    with pytest.raises(pcv.VerdictRevokedError, match="revoked"):
        authority["verifier"].verify(
            verdict["verdict_id"],
            expected_binding=authority["binding"],
        )


def test_expired_and_stale_verdicts_fail_closed(authority: dict) -> None:
    verdict = _issue(authority, ttl_seconds=180)
    authority["clock"].now += timedelta(seconds=181)
    with pytest.raises(pcv.VerdictExpiredError, match="expired"):
        authority["verifier"].verify(
            verdict["verdict_id"],
            expected_binding=authority["binding"],
        )

    other = authority.copy()
    other_private = pcv.generate_private_key()
    other_policy = pcv.VerdictPolicy(
        policy_version="product-closeout-v1",
        public_keys={"k": pcv.public_key_bytes(other_private)},
        max_verdict_age_seconds=5,
        max_ttl_seconds=300,
    )
    other_ledger = pcv.ProtectedVerdictLedger(
        authority["ledger"].path.parent / "stale.jsonl"
    )
    other_clock = MutableClock(datetime(2026, 7, 26, 12, 0, tzinfo=UTC))
    issuer = pcv.ProductCloseoutVerdictService(
        ledger=other_ledger,
        policy=other_policy,
        private_key=other_private,
        key_id="k",
        clock=other_clock,
    )
    stale = issuer.issue(
        authority["binding"],
        authority["actor"],
        decision="approved",
        ttl_seconds=300,
    )
    other_clock.now += timedelta(seconds=6)
    verifier = pcv.ProductCloseoutVerdictService(
        ledger=other_ledger,
        policy=other_policy,
        clock=other_clock,
    )
    with pytest.raises(pcv.VerdictExpiredError, match="stale"):
        verifier.verify(stale["verdict_id"], expected_binding=authority["binding"])


def test_tampered_ledger_and_standalone_candidate_verdict_are_rejected(
    authority: dict,
    tmp_path: Path,
) -> None:
    verdict = _issue(authority)
    raw = json.loads(authority["ledger"].path.read_text(encoding="utf-8"))
    raw["verdict"]["frontend_sha"] = "9" * 40
    authority["ledger"].path.write_text(
        json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(pcv.VerdictIntegrityError, match="record hash mismatch"):
        authority["verifier"].verify(
            verdict["verdict_id"],
            expected_binding=authority["binding"],
        )

    candidate = tmp_path / "candidate-verdict.json"
    candidate.write_text(json.dumps(verdict), encoding="utf-8")
    clean_ledger = pcv.ProtectedVerdictLedger(tmp_path / "empty-ledger.jsonl")
    clean_verifier = pcv.ProductCloseoutVerdictService(
        ledger=clean_ledger,
        policy=authority["policy"],
        clock=authority["clock"],
    )
    with pytest.raises(pcv.VerdictIntegrityError, match="exactly one issue"):
        clean_verifier.verify(
            verdict["verdict_id"],
            expected_binding=authority["binding"],
        )


def test_consumption_retry_is_idempotent_but_distinct_replay_is_rejected(
    authority: dict,
) -> None:
    verdict = _issue(authority)
    consumed = authority["verifier"].consume(
        verdict["verdict_id"],
        expected_binding=authority["binding"],
        transition="done",
        transition_actor="Codex",
        idempotency_key="done-L12-CLOSE-001-attempt-001",
    )
    assert consumed["record_type"] == "consumed"
    authority["verifier"].verify(
        verdict["verdict_id"],
        expected_binding=authority["binding"],
        consumption_state="consumed",
    )

    retried = authority["verifier"].consume(
        verdict["verdict_id"],
        expected_binding=authority["binding"],
        transition="done",
        transition_actor="Codex",
        idempotency_key="done-L12-CLOSE-001-attempt-001",
    )
    assert retried == consumed

    with pytest.raises(pcv.VerdictReplayError, match="different transition attempt"):
        authority["verifier"].consume(
            verdict["verdict_id"],
            expected_binding=authority["binding"],
            transition="done",
            transition_actor="Codex",
            idempotency_key="done-L12-CLOSE-001-attempt-002",
        )


def test_consumed_verdict_remains_auditable_after_ttl_expires(
    authority: dict,
) -> None:
    verdict = _issue(authority, ttl_seconds=30)
    authority["verifier"].consume(
        verdict["verdict_id"],
        expected_binding=authority["binding"],
        transition="done",
        transition_actor="Codex",
        idempotency_key="done-L12-CLOSE-001-attempt-001",
    )
    authority["clock"].now += timedelta(hours=2)

    assert authority["verifier"].verify(
        verdict["verdict_id"],
        expected_binding=authority["binding"],
        consumption_state="consumed",
    ) == verdict


def test_concurrent_done_consumption_has_exactly_one_winner(authority: dict) -> None:
    verdict = _issue(authority)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def consume(attempt: int) -> None:
        barrier.wait()
        try:
            authority["verifier"].consume(
                verdict["verdict_id"],
                expected_binding=authority["binding"],
                transition="done",
                transition_actor="Codex",
                idempotency_key=f"concurrent-done-attempt-{attempt}",
            )
        except pcv.VerdictReplayError:
            results.append("replay")
        else:
            results.append("consumed")

    threads = [
        threading.Thread(target=consume, args=(attempt,))
        for attempt in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(results) == ["consumed", "replay"]
    assert authority["verifier"].inspect(verdict["verdict_id"])[
        "consumption_count"
    ] == 1


def test_concurrent_duplicate_decision_and_nonce_have_one_winner(authority: dict) -> None:
    barrier = threading.Barrier(2)
    results: list[str] = []

    def issue() -> None:
        barrier.wait()
        try:
            _issue(authority)
        except pcv.VerdictConflictError:
            results.append("conflict")
        else:
            results.append("issued")

    threads = [threading.Thread(target=issue) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(results) == ["conflict", "issued"]
    assert len(authority["ledger"].records()) == 1


def test_concurrent_distinct_decisions_for_one_binding_admit_one_verdict(
    authority: dict,
) -> None:
    barrier = threading.Barrier(2)
    results: list[str] = []

    def issue(attempt: int) -> None:
        barrier.wait()
        try:
            _issue(
                authority,
                decision="approved" if attempt == 0 else "rejected",
                verdict_id=f"pclose-competing-{attempt}",
                nonce=f"nonce-competing-{attempt}",
            )
        except pcv.VerdictConflictError:
            results.append("conflict")
        else:
            results.append("issued")

    threads = [
        threading.Thread(target=issue, args=(attempt,)) for attempt in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(results) == ["conflict", "issued"]
    issued = [
        record
        for record in authority["ledger"].records()
        if record["record_type"] == "issued"
    ]
    assert len(issued) == 1


def test_second_decision_for_one_binding_is_rejected_while_the_first_stands(
    authority: dict,
) -> None:
    first = _issue(authority, decision="approved")

    with pytest.raises(pcv.VerdictConflictError, match="active closeout decision"):
        _issue(
            authority,
            decision="rejected",
            verdict_id="pclose-test-002",
            nonce="nonce-product-closeout-002",
        )

    assert authority["verifier"].verify(
        first["verdict_id"],
        expected_binding=authority["binding"],
    ) == first


def test_a_different_binding_may_hold_its_own_active_decision(authority: dict) -> None:
    _issue(authority)
    other_values = authority["binding"].to_dict()
    other_values["task_id"] = "L12-OTHER-001"
    other_binding = pcv.CloseoutBinding.from_mapping(other_values)

    other = authority["service"].issue(
        other_binding,
        authority["actor"],
        decision="approved",
        ttl_seconds=180,
        verdict_id="pclose-test-other",
        nonce="nonce-product-closeout-other",
    )

    assert authority["verifier"].verify(
        other["verdict_id"],
        expected_binding=other_binding,
    ) == other


def test_expired_or_revoked_decision_allows_an_exact_replacement(
    authority: dict,
) -> None:
    expired = _issue(authority, ttl_seconds=60)
    authority["clock"].now += timedelta(seconds=61)
    replacement = _issue(
        authority,
        verdict_id="pclose-test-replacement-001",
        nonce="nonce-product-closeout-replacement-001",
    )
    assert authority["verifier"].verify(
        replacement["verdict_id"],
        expected_binding=authority["binding"],
    ) == replacement
    with pytest.raises(pcv.VerdictExpiredError, match="expired"):
        authority["verifier"].verify(
            expired["verdict_id"],
            expected_binding=authority["binding"],
        )

    authority["service"].revoke(
        replacement["verdict_id"],
        authority["actor"],
        reason="frontend deployment identity changed",
    )
    after_revocation = _issue(
        authority,
        verdict_id="pclose-test-replacement-002",
        nonce="nonce-product-closeout-replacement-002",
    )
    assert authority["verifier"].verify(
        after_revocation["verdict_id"],
        expected_binding=authority["binding"],
    ) == after_revocation


def test_protected_paths_must_be_absolute_external_and_symlink_free(
    tmp_path: Path,
) -> None:
    with pytest.raises(pcv.VerdictIntegrityError, match="absolute"):
        pcv.ProtectedVerdictLedger(Path("candidate-ledger.jsonl"))

    candidate_root = tmp_path / "repo"
    candidate_root.mkdir()
    with pytest.raises(pcv.VerdictIntegrityError, match="outside"):
        pcv.ProtectedVerdictLedger(
            candidate_root / "ledger.jsonl",
            forbidden_roots=[candidate_root],
        )

    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "ledger-link"
    link.symlink_to(external, target_is_directory=True)
    with pytest.raises(pcv.VerdictIntegrityError, match="symlink"):
        pcv.ProtectedVerdictLedger(link / "ledger.jsonl")


def test_external_policy_loader_is_verification_only(
    authority: dict,
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "protected-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "policy_version": authority["policy"].policy_version,
                "ledger_path": str(authority["ledger"].path),
                "public_keys": {
                    "human-ops-key-1": pcv.public_key_to_base64(
                        authority["policy"].public_keys["human-ops-key-1"]
                    )
                },
                "max_verdict_age_seconds": 120,
                "max_ttl_seconds": 300,
            }
        ),
        encoding="utf-8",
    )
    verdict = _issue(authority)

    verifier = pcv.load_verifier_service(policy_path=policy_path)
    assert verifier.private_key is None
    verifier.verify(
        verdict["verdict_id"],
        expected_binding=authority["binding"],
        now=authority["clock"].now,
    )
    with pytest.raises(pcv.VerdictAuthorizationError, match="no protected issuer"):
        verifier.issue(
            authority["binding"],
            authority["actor"],
            decision="approved",
        )
