"""Isolated receiver tests using the frozen VM candidate receipt wire contract.

These independently constructed fixtures are not image observations or hosted
release evidence. No sibling test import, network, daemon or lease acquisition.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import stat

import pytest

from scripts import dev_candidate_receipt as receiver


BACKEND = "a" * 40
LEASE = "11111111-1111-4111-8111-111111111111"


def reseal(result):
    record = result["candidate_image_manifest"]
    override = {"services": {service: {"image": row["image_id"], "pull_policy": "never"}
                             for service, row in record["services"].items()}}
    record["image_override_sha256"] = receiver.digest(receiver.encoded(override))
    result["candidate_image_override_sha256"] = record["image_override_sha256"]
    result["candidate_image_manifest_sha256"] = receiver.digest(receiver.encoded(record))
    return result


@pytest.fixture
def documents():
    identity = {"candidate_id": "b" * 64, "run_id": "12345", "attempt": "2",
                "controller_sha": "c" * 40, "candidate_backend_sha": BACKEND,
                "candidate_frontend_sha": "d" * 40, "previous_backend_sha": "e" * 40,
                "previous_frontend_sha": "f" * 40}
    context = {"schema_version": "pantheon.dev-candidate-receipt-context.v1",
               "identity": identity, "baseline_manifest_sha256": "1" * 64, "guard_lease_id": LEASE}
    record = {"schema_version": "pantheon.dev-candidate-image-admission.v1", "environment": "dev",
              "project_id": "pantheon-dev-20260902", "vm": "pantheon-dev-deploy",
              "identity": deepcopy(identity), "seal_lease_id": LEASE,
              "sealed_at": "2026-09-09T03:00:00Z", "baseline_manifest_sha256": "1" * 64,
              "candidate_compose_sha256": "2" * 64,
              "services": {service: {"image_id": "sha256:" + str(number) * 64,
                                      "oci_revision": BACKEND,
                                      "git_sha": None if service == "loop-run-projector-scheduler" else BACKEND,
                                      "compose_image": "pantheon-" + service}
                           for number, service in enumerate(receiver.SERVICES, start=3)}}
    folder = receiver.ARTIFACT_ROOT / f"baseline-12345-2-{identity['candidate_id']}"
    result = {"candidate_image_manifest_path": str(folder / "candidate-images.json"),
              "candidate_image_manifest": record,
              "candidate_image_override_path": str(folder / "candidate-images.override.json")}
    return context, reseal(result)


def observer(tmp_path, context):
    directory = tmp_path / "private-receipt"
    directory.mkdir(mode=0o700)
    context_path = directory / "context.json"
    context_path.write_bytes(receiver.encoded(context))
    context_path.chmod(0o600)
    output = directory / "candidate-result.json"
    return receiver.CandidateReceiptObserver(context_path=context_path, output_path=output), context_path, output


def line(result):
    return receiver.PREFIX + receiver.encoded(result)


def test_valid_frozen_contract_returns_exact_receipt_digest(documents):
    context, result = documents
    assert receiver.validate_receipt(result, context) == result["candidate_image_manifest_sha256"]


@pytest.mark.parametrize("field,value", [("schema_version", "wrong"), ("guard_lease_id", "invalid"),
                                        ("baseline_manifest_sha256", "short"), ("identity", {})])
def test_invalid_context_rejected_at_construction_before_transport(documents, tmp_path, field, value):
    context, _ = documents
    context[field] = value
    with pytest.raises((receiver.CaptureError, ValueError)):
        observer(tmp_path, context)


def test_finalize_saved_receipt_emits_only_validated_nonsecret_outputs(documents, tmp_path):
    context, result = documents
    receive, context_path, receipt = observer(tmp_path, context)
    receive(line(result))
    output = tmp_path / "github-output"
    receiver.finalize(context_path, receipt, output)
    actual = dict(row.split("=", 1) for row in output.read_text().splitlines())
    assert actual == {**{key: result[key] for key in (
        "candidate_image_manifest_path", "candidate_image_manifest_sha256",
        "candidate_image_override_path", "candidate_image_override_sha256")}, "receipt_file": str(receipt)}


@pytest.mark.parametrize("failure", ["missing", "different-encoding", "wrong-source"])
def test_finalize_does_not_repair_or_invent_a_receipt(documents, tmp_path, failure):
    context, result = documents
    receive, context_path, receipt = observer(tmp_path, context)
    if failure != "missing":
        receive(line(result))
        if failure == "different-encoding":
            receipt.write_text(json.dumps(result, indent=2))
        else:
            result["candidate_image_manifest"]["identity"]["candidate_backend_sha"] = "0" * 40
            receipt.write_bytes(receiver.encoded(reseal(result)))
    output = tmp_path / "github-output"
    assert receiver.main(["--context", str(context_path), "--receipt", str(receipt),
                          "--github-output", str(output)]) == 75
    assert not output.exists()


@pytest.mark.parametrize("field", ["candidate_id", "run_id", "attempt", "controller_sha", "candidate_backend_sha",
                                   "candidate_frontend_sha", "previous_backend_sha", "previous_frontend_sha"])
def test_every_exact_identity_field_is_bound_to_context(documents, field):
    context, result = documents
    changed = "8" * len(context["identity"][field])
    context["identity"][field] = changed
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("field,value", [
    ("candidate_id", "sha256:" + "a" * 64), ("run_id", "0"), ("run_id", "01"),
    ("attempt", "-1"), ("controller_sha", "A" * 40), ("candidate_backend_sha", "short"),
    ("candidate_frontend_sha", None), ("previous_backend_sha", 123), ("previous_frontend_sha", "g" * 40),
])
def test_invalid_context_identity_is_not_an_ack_authority(documents, field, value):
    context, result = documents
    context["identity"][field] = value
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("field,value", [
    ("schema_version", "wrong-context"), ("baseline_manifest_sha256", "9" * 64),
    ("guard_lease_id", "22222222-2222-4222-8222-222222222222"),
])
def test_context_schema_baseline_and_guard_are_exact(documents, field, value):
    context, result = documents
    context[field] = value
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("field,value", [
    ("schema_version", "wrong-receipt"), ("environment", "production"),
    ("project_id", "unrelated-project"), ("vm", "unrelated-vm"),
    ("seal_lease_id", "22222222-2222-4222-8222-222222222222"),
    ("baseline_manifest_sha256", "9" * 64), ("candidate_compose_sha256", "short"),
    ("sealed_at", "yesterday"), ("sealed_at", "2026-99-99T99:99:99Z"),
])
def test_record_schema_target_provenance_and_source_contract(documents, field, value):
    context, result = documents
    result["candidate_image_manifest"][field] = value
    reseal(result)
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("level", ["context", "identity", "result", "record", "service"])
def test_unknown_fields_fail_closed_even_when_wrapper_hash_is_updated(documents, level):
    context, result = documents
    record = result["candidate_image_manifest"]
    target = {"context": context, "identity": context["identity"], "result": result,
              "record": record, "service": record["services"]["operator-bff"]}[level]
    target["unexpected"] = "fixture"
    reseal(result)
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("field,value", [
    ("image_id", "pantheon-operator-bff:latest"), ("oci_revision", "9" * 40),
    ("git_sha", None), ("git_sha", "9" * 40), ("compose_image", "pantheon-governance"),
])
@pytest.mark.parametrize("service", ["operator-bff", "agora-interaction-worker"])
def test_bff_images_require_exact_owned_service_and_baked_source(documents, service, field, value):
    context, result = documents
    result["candidate_image_manifest"]["services"][service][field] = value
    reseal(result)
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("git_sha", [None, BACKEND])
def test_projector_optional_baked_git_sha_matches_frozen_driver_contract(documents, git_sha):
    context, result = documents
    result["candidate_image_manifest"]["services"]["loop-run-projector-scheduler"]["git_sha"] = git_sha
    reseal(result)
    assert receiver.validate_receipt(result, context) == result["candidate_image_manifest_sha256"]


def test_projector_cannot_carry_another_baked_source(documents):
    context, result = documents
    result["candidate_image_manifest"]["services"]["loop-run-projector-scheduler"]["git_sha"] = "9" * 40
    reseal(result)
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("mutation", ["missing", "extra-owner"])
def test_service_set_is_exactly_the_three_bff_owned_processes(documents, mutation):
    context, result = documents
    services = result["candidate_image_manifest"]["services"]
    if mutation == "missing":
        del services["agora-interaction-worker"]
    else:
        services["governance"] = deepcopy(services["operator-bff"])
    reseal(result)
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("field", ["candidate_image_manifest_sha256", "candidate_image_override_sha256"])
def test_wrapper_hash_mismatch_is_rejected(documents, field):
    context, result = documents
    result[field] = "9" * 64
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


def test_override_is_rederived_from_only_exact_images_and_pull_never(documents):
    context, result = documents
    record = result["candidate_image_manifest"]
    unsafe_override = {"services": {service: {"image": row["image_id"], "pull_policy": "always"}
                                    for service, row in record["services"].items()}}
    record["image_override_sha256"] = receiver.digest(receiver.encoded(unsafe_override))
    result["candidate_image_override_sha256"] = record["image_override_sha256"]
    result["candidate_image_manifest_sha256"] = receiver.digest(receiver.encoded(record))
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


@pytest.mark.parametrize("field", ["candidate_image_manifest_path", "candidate_image_override_path"])
@pytest.mark.parametrize("mutation", ["tmp", "other-run", "traversal"])
def test_receipt_paths_are_bound_to_fixed_run_directory(documents, field, mutation):
    context, result = documents
    path = result[field]
    if mutation == "tmp":
        path = "/tmp/" + Path(path).name
    elif mutation == "other-run":
        path = path.replace("baseline-12345-2-", "baseline-12346-2-")
    else:
        path = str(Path(path).parent / ".." / Path(path).name)
    result[field] = path
    with pytest.raises(receiver.CaptureError):
        receiver.validate_receipt(result, context)


def test_ack_only_after_receipt_and_directory_are_fsynced(documents, tmp_path, monkeypatch):
    context, result = documents
    receive, _, output = observer(tmp_path, context)
    events = []
    original = os.fsync

    def fsync(fd):
        assert not receive.received
        assert output.read_bytes() == receiver.encoded(result)
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "receipt")
        original(fd)

    monkeypatch.setattr(receiver.os, "fsync", fsync)
    ack = receive(line(result))
    assert events == ["receipt", "directory"]
    assert receive.received
    assert ack == result["candidate_image_manifest_sha256"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_bytes()) == result


@pytest.mark.parametrize("stage", ["receipt", "directory"])
def test_fsync_failure_never_returns_ack_or_allows_duplicate_overwrite(documents, tmp_path, monkeypatch, stage):
    context, result = documents
    receive, _, output = observer(tmp_path, context)
    original = os.fsync

    def fsync(fd):
        current = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "receipt"
        if current == stage:
            raise OSError("synthetic fsync failure")
        original(fd)

    monkeypatch.setattr(receiver.os, "fsync", fsync)
    with pytest.raises(OSError):
        receive(line(result))
    assert not receive.received
    assert output.exists()  # retained evidence of failure, never silently replaced
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        receive(line(result))
    assert output.read_bytes() == before
    assert not receive.received


def test_output_creation_failure_never_acks(documents, tmp_path, monkeypatch):
    context, result = documents
    receive, _, output = observer(tmp_path, context)
    original = os.open

    def fail_output(path, *args, **kwargs):
        if Path(path) == output:
            raise OSError("synthetic full disk")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(receiver.os, "open", fail_output)
    with pytest.raises(OSError):
        receive(line(result))
    assert not receive.received
    assert not output.exists()


def test_receipt_write_failure_never_reaches_fsync_or_ack(documents, tmp_path, monkeypatch):
    context, result = documents
    receive, _, output = observer(tmp_path, context)
    original = os.fdopen

    class FailedWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, raw):
            raise OSError("synthetic write failure")

        def __exit__(self, *_):
            self.stream.close()

    def fdopen(fd, mode, *args, **kwargs):
        stream = original(fd, mode, *args, **kwargs)
        return FailedWriter(stream) if mode == "wb" else stream

    monkeypatch.setattr(receiver.os, "fdopen", fdopen)
    monkeypatch.setattr(receiver.os, "fsync", lambda _: pytest.fail("must not fsync failed write"))
    with pytest.raises(OSError):
        receive(line(result))
    assert not receive.received
    assert output.exists() and output.stat().st_size == 0


def test_duplicate_receipt_fails_and_retains_original_bytes(documents, tmp_path):
    context, result = documents
    receive, context_path, output = observer(tmp_path, context)
    receive(line(result))
    before = output.read_bytes()
    with pytest.raises(receiver.CaptureError):
        receive(line(result))
    assert output.read_bytes() == before
    with pytest.raises(receiver.CaptureError):
        receiver.CandidateReceiptObserver(context_path=context_path, output_path=output)


def test_nonreceipt_output_is_ignored_without_writing_or_acking(documents, tmp_path):
    context, _ = documents
    receive, _, output = observer(tmp_path, context)
    assert receive(b"safe ordinary command output\n") is None
    assert not receive.received
    assert not output.exists()


@pytest.mark.parametrize("kind", ["duplicate-json", "invalid-json", "oversized", "unknown-wrapper-field"])
def test_malformed_receipt_never_creates_output(documents, tmp_path, kind):
    context, result = documents
    receive, _, output = observer(tmp_path, context)
    if kind == "duplicate-json":
        data = receiver.PREFIX + b'{"candidate_image_manifest_path":"first",' + receiver.encoded(result)[1:]
    elif kind == "invalid-json":
        data = receiver.PREFIX + b"{not json}\n"
    elif kind == "oversized":
        data = receiver.PREFIX + b"x" * 65537
    else:
        data = line({**result, "extra": "fixture"})
    with pytest.raises((receiver.CaptureError, ValueError)):
        receive(data)
    assert not output.exists()
    assert not receive.received


@pytest.mark.parametrize("target", ["context", "directory"])
def test_context_and_receipt_directory_must_be_private(documents, tmp_path, target):
    context, _ = documents
    _, context_path, output = observer(tmp_path, context)
    (context_path if target == "context" else context_path.parent).chmod(0o644 if target == "context" else 0o755)
    with pytest.raises(receiver.CaptureError):
        receiver.CandidateReceiptObserver(context_path=context_path, output_path=output)


@pytest.mark.parametrize("target", ["context", "directory", "output"])
def test_symlink_context_parent_or_output_is_rejected(documents, tmp_path, target):
    context, _ = documents
    _, context_path, output = observer(tmp_path, context)
    if target == "output":
        output.symlink_to(tmp_path / "must-not-create")
    else:
        path = context_path if target == "context" else context_path.parent
        saved = path.with_name(path.name + "-original")
        path.rename(saved)
        path.symlink_to(saved, target_is_directory=target == "directory")
    with pytest.raises((receiver.CaptureError, OSError)):
        receiver.CandidateReceiptObserver(context_path=context_path, output_path=output)


def test_duplicate_context_json_is_rejected(documents, tmp_path):
    context, _ = documents
    _, context_path, output = observer(tmp_path, context)
    context_path.write_bytes(b'{"schema_version":"duplicate",' + receiver.encoded(context)[1:])
    with pytest.raises(receiver.CaptureError):
        receiver.CandidateReceiptObserver(context_path=context_path, output_path=output)


def test_context_input_size_bound_and_fifo_rejected_without_blocking(documents, tmp_path):
    context, _ = documents
    _, context_path, output = observer(tmp_path, context)
    context_path.write_bytes(b"x" * 65537)
    with pytest.raises(receiver.CaptureError):
        receiver.CandidateReceiptObserver(context_path=context_path, output_path=output)
    fifo = context_path.with_name("context.fifo")
    os.mkfifo(fifo, mode=0o600)
    with pytest.raises(receiver.CaptureError):
        receiver.CandidateReceiptObserver(context_path=fifo, output_path=output)


def test_context_file_owner_is_checked_on_open_descriptor(documents, tmp_path, monkeypatch):
    context, _ = documents
    _, context_path, output = observer(tmp_path, context)
    original = os.fstat

    def fstat(fd):
        fields = list(original(fd))
        fields[4] = os.geteuid() + 1
        return os.stat_result(fields)

    monkeypatch.setattr(receiver.os, "fstat", fstat)
    with pytest.raises(receiver.CaptureError):
        receiver.CandidateReceiptObserver(context_path=context_path, output_path=output)


def test_receipt_directory_owner_is_checked(documents, tmp_path, monkeypatch):
    context, _ = documents
    _, context_path, output = observer(tmp_path, context)
    actual_uid = os.geteuid()
    monkeypatch.setattr(receiver.os, "geteuid", lambda: actual_uid + 1)
    with pytest.raises(receiver.CaptureError):
        receiver.CandidateReceiptObserver(context_path=context_path, output_path=output)
