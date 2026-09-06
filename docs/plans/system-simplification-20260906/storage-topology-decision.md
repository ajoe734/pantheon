# Storage Topology Decision — OSS-INFRA-CHOICE-001

Status: accepted local source-development decision (choice only; no hosted
migration, no data movement, no Compose edit performed by this task).
Date: 2026-09-06.
Depends on: OSS-COVERAGE-PLAN-001 (accepted; `docs/plans/system-simplification-20260906/`
carries the frozen inventory this decision is built from).
Consumed by: `OSS-INFRA-PROFILES-001` (Compose profile convergence — the sole
later Compose writer) and `OSS-OBJECT-STORE-CUTOVER-001` (implements this
exact choice).

## 1. What actually consumes object storage today

Full enumeration: [`storage-consumer-matrix.json`](storage-consumer-matrix.json).

Findings, all read-only, all reproducible from this repo:

1. **MinIO/S3 has zero real *source-code* consumers; its hosted content is
   unknown, not proven empty.** `docker-compose.yml` and
   `docker-compose.control.yml` each declare a `minio` server + `minio-init`
   bootstrap and wire `PANTHEON_S3_ENDPOINT` / `PANTHEON_S3_ACCESS_KEY` /
   `PANTHEON_S3_SECRET_KEY` / `PANTHEON_ARTIFACT_BUCKET` into 18 (main stack)
   / 12 (control stack) application services. A repo-wide, non-test search
   for `boto3`, `botocore`, or any S3 client construction under `services/`
   and `scripts/` returns nothing. The only two files that read those four
   env vars — `services/foundation/persistence_posture.py` and
   `services/source_search_posture.py` — check only that the strings are
   non-empty when the staging/prod posture is enforced; by their own
   docstrings, "It does not open database or object-store connections."
   No application code path writes to or reads from MinIO. This task did
   **not** connect to a hosted MinIO instance or list its objects, so
   whether any hosted `minio-data` volume already holds real objects
   (written by hand, by `mc`, or by a since-removed code path) is unknown,
   not established as empty — retirement below is conditioned on that
   inventory being confirmed later, not on this source-only finding alone.
2. **The one in-request-path object-store consumer already uses a managed
   backend, not MinIO.** `services/control-plane/bff/management_ai_store.py`
   (`ManagementAiAttachmentStore`) uploads/downloads management-AI chat
   attachments through `google-cloud-storage` (`storage.Client().bucket(...)
   .blob(...)`), gated by `PANTHEON_MGMT_AI_ATTACH_BUCKET`. When that bucket
   env var is unset (the default in both compose files today) it falls back
   to local filesystem storage, never to MinIO. This code path already has
   an isolated round-trip proof:
   `services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py::
   test_management_ai_attachment_store_uses_gcs_bucket_metadata`.
3. **Two more real GCS consumers exist outside the request path, both
   already GCS, neither MinIO.** `scripts/capture_canonical_telemetry_baseline.py`
   (lines ~159-183) resolves a `gcs_object` recovery source via
   `gcloud storage objects describe` and hard-fails unless the object
   returns a `generation`, a `metageneration` (both GCS-automatic, per
   write), and a valid hex `pantheon_sha256`/`sha256` metadata value
   (custom object metadata the uploader must set explicitly; GCS does
   not compute this) — a real reliance on GCS's built-in per-write
   version identity plus a call-site metadata contract. `scripts/deploy_nonprod_vm.sh`
   (lines ~2020-2058) performs a real PUT/GET/DELETE probe against the
   hosted `PANTHEON_MGMT_AI_ATTACH_BUCKET` bucket at deploy time via the
   GCS JSON API. Both already target GCS, so neither motivates keeping or
   replacing MinIO; both do mean "the one real consumer" understates the
   estate — see `storage-consumer-matrix.json`'s `real_object_store_consumers`
   array for the full, corrected list of three.

No service declares a bucket-level retention policy, legal-hold flag,
bucket versioning flag, server-side-encryption key, or per-tenant
bucket/prefix partition for either backend; the existing partition scheme
is a `session_id/turn_id` path prefix inside one shared bucket. Tenant
isolation for attachment reads *is* enforced, just above the storage layer:
`services/control-plane/bff/main.py`'s `bff_management_ai_attachment`
handler (lines ~17354-17383) resolves the caller's tenant identity and,
via `_management_ai_get_session_or_404` (main.py:13536-13547), 404s only
if the attachment or its owning session does not exist at all; if the
session exists but the caller is neither its owner nor tenant-matched,
`_management_ai_require_session_access` (main.py:13499-13508) instead
raises 403 FORBIDDEN before the object is read — any cutover must keep
routing reads through that same check, including this exact
404-for-nonexistent vs 403-for-unauthorized split, not merely preserve
object bytes. There is no MinIO-specific
backup/restore runbook — MinIO objects fall under the same generic
VM-disk-snapshot policy as every other stateful backend
(`docs/deployment/vm-dev-staging-prod-management-plan.md` §11.3), not a
bucket-level restore procedure.

## 2. Upstream maintenance re-check

Per `docs/reviews/2026-09-06-system-simplification/oss-findings.md` (line
34, produced under `OSS-COVERAGE-PLAN-001`): the pinned MinIO server digest
(`minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`)
sits behind an upstream community repository archived 2026-04-25 — no
longer maintained, source-only distribution going forward, legacy binaries
unmaintained. The two Compose stacks already disagree on the `mc` client
image (`minio/mc:RELEASE.2024-01-16T16-06-34Z` in `docker-compose.yml` vs
`minio/mc:latest` in `docker-compose.control.yml`); "upgrade MinIO to
latest" cannot restore a maintained artifact because there is no maintained
upstream release channel left to track. MinIO server is AGPLv3; the GCS
client (`google-cloud-storage>=2.16,<3.0`, Apache-2.0) is already a pinned
dependency of `services/control-plane/bff/requirements.txt`.

## 3. Options considered (at most three, per acceptance)

### Option A — Replace MinIO with another maintained local S3-compatible server

Concrete candidates exist (e.g. SeaweedFS, Garage) that are actively
maintained and speak the S3 API. This would preserve the current
`PANTHEON_S3_*` shape and require no code change in the two posture files.
**Rejected**: it would spend real engineering effort (new server image,
new health checks, new backup story) standing up a maintained replacement
for an S3 API that no application source code calls. There is no S3
semantics to prove for a consumer that does not exist in source, and it
does nothing to help any of the three real GCS consumers in §1.

### Option B — Standardize on the already-adopted managed object store (GCS)

The BFF attachment path already runs on Google Cloud Storage, the
environment topology is entirely GCP-based (`docs/04/pantheon_environment_closure_sa_sd_2026-09/SA.md`:
GCP projects, Workload Identity Federation, no Kubernetes), and
`RESOURCE_AND_COST_MODEL.md` already budgets a Cloud Storage line item
(~$6.00/month for 150 GB multi-regional, snapshots/evidence) inside the
accepted cost model. Choosing GCS as the one object-store backend needs no
new infrastructure decision — it is already running — and the isolated
proof already exists and passes (§4). Any *new* object-store need that
resembles the attachment use case (durable blob keyed by an
opaque id, read back by URL) reuses `ManagementAiAttachmentStore`'s pattern
instead of introducing a second backend.

### Option C — Documented retirement (no real consumer/data remains)

Applies cleanly to the **MinIO/S3 layer specifically**, not to "object
storage" as a concept: zero application services perform S3 I/O against
it. Whether the hosted `minio-data` volume currently holds anything beyond
the bucket `minio-init` creates at boot is unknown per §1 — this task did
not inspect it — so retirement here is conditional: the later cutover task
must confirm (by its own reversible local migration/readback check) that
no populated hosted content needs inventory or restore before deleting the
server/volume. Retirement is not "delete object storage" — the three real
GCS workloads in §1 keep their already-chosen paths unchanged. This option
is folded into the chosen disposition below rather than treated as a
separate branch, since it only concerns a source-code layer that already
has zero source-code consumers, conditioned on that hosted-content
confirmation.

## 4. Reproducible isolated proof

Ran directly against the production adapter, no network, no hosted
credentials, from the task worktree:

```
$ .venv-pantheon/bin/python3 - <<'PY'
import sys, base64
sys.path.insert(0, "services/control-plane")
import bff.management_ai_store as mas

class FakeBlob:
    def __init__(self, name): self.name = name
    def upload_from_string(self, content, content_type=None):
        uploads[self.name] = (content, content_type)
    def download_as_bytes(self): return uploads[self.name][0]

class FakeBucket:
    def blob(self, name): return FakeBlob(name)

uploads = {}
store = mas.ManagementAiAttachmentStore(storage_path="off", bucket_name="pantheon-test-attachments")
store._gcs_bucket = lambda bucket_name=None: FakeBucket()

image_bytes = b"\x89PNG\r\n\x1a\nstored-in-fake-gcs"
metadata = store.store_inline_attachment(
    {"kind": "image", "mimeType": "image/png", "filename": "screen.png",
     "dataBase64": base64.b64encode(image_bytes).decode("ascii")},
    session_id="mgmt-gcs-session", turn_id="turn-gcs",
)
assert metadata["storageUrl"].startswith("gs://pantheon-test-attachments/management-ai-attachments/")
assert metadata["sizeBytes"] == len(image_bytes)
content, mime_type, filename = store.read(metadata["id"], metadata)
assert content == image_bytes and mime_type == "image/png" and filename == "screen.png"
print("ISOLATED_GCS_OBJECT_STORE_PROOF: PASS", metadata["objectName"])
PY
ISOLATED_GCS_OBJECT_STORE_PROOF: PASS management-ai-attachments/mgmt-gcs-session/turn-gcs/att_48f3e55c76604878-screen.png
```

This proves object put-by-key, get-by-key, size, content-type, and
filename round-trip through the exact adapter code that would run in
production against a GCS bucket. The repository also carries a
pre-existing equivalent test,
`services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py::
test_management_ai_attachment_store_uses_gcs_bucket_metadata`, asserting
the same contract; collecting that file standalone via `pytest` currently
fails in this worktree on an unrelated pytest import-mode conflict in
`main.py`'s package resolution (`ImportError: attempted relative import
with no known parent package` when `main.py` pulls in `.models`), which is
a pre-existing BFF test-harness issue orthogonal to storage and is not
touched by this decision-only task. The equivalent logic was proven
directly against `management_ai_store.py` above without going through
`main.py`.

A second, independent proof exercises the adapter's *real* local-disk
fallback path (no mock objects at all) to prove genuine durable
restore/readback, seeded and read back across two separate store
instances *within the same process* so no in-process cache can mask a
fake pass. This proves readback is driven by the bytes actually
persisted to disk rather than a shared in-memory object, but it is not a
fresh-process restart/restore proof (both instances run in the one
Python process invoked below):

```
$ .venv-pantheon/bin/python3 - <<'PY'
import sys, base64, tempfile, hashlib, os
sys.path.insert(0, "services/control-plane")
import bff.management_ai_store as mas

tmp_dir = tempfile.mkdtemp(prefix="pantheon-storage-restore-proof-")
writer = mas.ManagementAiAttachmentStore(storage_path=tmp_dir, bucket_name=None)
image_bytes = b"\x89PNG\r\n\x1a\nstored-on-real-local-disk-durable-restore-proof"
expected_sha256 = hashlib.sha256(image_bytes).hexdigest()
metadata = writer.store_inline_attachment(
    {"kind": "image", "mimeType": "image/png", "filename": "seeded.png",
     "dataBase64": base64.b64encode(image_bytes).decode("ascii")},
    session_id="restore-proof-session", turn_id="restore-proof-turn",
)
on_disk_path = tmp_dir + "/" + metadata["id"] + ".bin"
assert os.path.isfile(on_disk_path)
with open(on_disk_path, "rb") as fh:
    assert hashlib.sha256(fh.read()).hexdigest() == expected_sha256

# A second, independent store instance (same process, no shared Python
# object) -- proves genuine readback of already-persisted disk bytes,
# not an in-process cache hit. This is not a fresh-process restart.
reader = mas.ManagementAiAttachmentStore(storage_path=tmp_dir, bucket_name=None)
content, mime_type, filename = reader.read(metadata["id"], metadata)
assert content == image_bytes
assert hashlib.sha256(content).hexdigest() == expected_sha256
assert mime_type == "image/png" and filename == "seeded.png"
print("ISOLATED_LOCAL_DURABLE_RESTORE_PROOF: PASS", metadata["id"], expected_sha256)
PY
ISOLATED_LOCAL_DURABLE_RESTORE_PROOF: PASS att_7c36d41b401f4422 be0795f6644bbf97091b21f7456335fb9181473d1795c92f719c9192c9cd961e
```

Together the two proofs cover every S3-shaped semantic that the
in-request-path attachment consumer actually needs today: put-by-key,
get-by-key, size, content-type, filename, and durable disk persistence
verified via a second same-process store instance (not a fresh-process
restart — see §4 above). They do **not** cover the GCS
`generation`/`metageneration` (GCS-automatic) plus `pantheon_sha256`
(custom, uploader-set) metadata identity contract required by
`scripts/capture_canonical_telemetry_baseline.py` (§1 finding 3): that
contract combines GCS's own built-in per-write versioning feature
(generation/metageneration, needing no new server-side capability) with
a custom metadata field the writer must set explicitly, and exercising
either against a *real* bucket requires hosted `gcloud`/network
credentials this read-only task does not have. That feasibility is left as an explicit open item for
`OSS-OBJECT-STORE-CUTOVER-001` — it is not fabricated as already-proven
here, and it is not a reason to prefer MinIO, since MinIO does not supply
this GCS-specific identity contract either.

Migration/restore feasibility: no application source code writes to the
MinIO layer, so no *known* data needs migrating out of it, but whether the
hosted `minio-data` volume itself is empty is unverified (§1) — the
cutover task's own inventory/readback check must confirm that before any
deletion. Restore feasibility for the chosen GCS path (both the
in-request-path attachment store and the two out-of-band consumers in §1
finding 3) is inherited from GCS's own managed durability/versioning
features; the local-disk-fallback proof above additionally shows the
non-GCS fallback path is independently durable on its own terms.

Operational cost: GCS is already inside the accepted cost model
(~$6/month, §3 Option B); no new spend is introduced by this choice.
Removing the MinIO server/init containers reduces the `core`/service
footprint measured in `RESOURCE_AND_COST_MODEL.md` and
`docs/reviews/2026-09-06-system-simplification/oss-findings.md`
(image/digest/provenance debt item #5), but that removal is Compose work
reserved for `OSS-INFRA-PROFILES-001` / `OSS-OBJECT-STORE-CUTOVER-001`, not
this task.

## 5. Decision

**Adopt Option B + fold in Option C:** Google Cloud Storage (already
integrated via `ManagementAiAttachmentStore`, already budgeted, already
proven in isolation, and already the target of the two out-of-band
consumers in §1 finding 3) is the one accepted object-store backend for
Pantheon going forward. The self-hosted MinIO/S3 layer is accepted for
retirement because it has zero real source-code consumers today — not
because object storage in general is being removed, and not because its
hosted content is confirmed empty (that remains conditional per §1/§3).

**Exact scope for the later cutover task (`OSS-OBJECT-STORE-CUTOVER-001`):**
- Reuse `ManagementAiAttachmentStore` / the `google-cloud-storage` client
  as the sole in-request-path object-store adapter; do not introduce a
  second storage abstraction. `scripts/capture_canonical_telemetry_baseline.py`
  and `scripts/deploy_nonprod_vm.sh` keep their existing direct
  `gcloud`/GCS-JSON-API calls unchanged — they are out-of-band tooling, not
  part of the adapter this task is scoping.
- Preserve the `bff_management_ai_attachment` tenant/session authorization
  check (`services/control-plane/bff/main.py:17354-17383`) and the
  `generation`/`metageneration`/`pantheon_sha256` identity contract read by
  `scripts/capture_canonical_telemetry_baseline.py`; neither is a bucket
  configuration change, both are call-site contracts that must keep working
  unchanged.
- Before deleting anything, run a hosted inventory check confirming the
  `minio-data` volume and any `PANTHEON_MGMT_AI_ATTACH_BUCKET`-configured
  hosted bucket hold no data that still needs a restore/backfill path; this
  task explicitly leaves that inventory as a pending, un-fabricated
  dependent action, not a claimed-done result.
- Remove the `PANTHEON_S3_ENDPOINT` / `PANTHEON_S3_ACCESS_KEY` /
  `PANTHEON_S3_SECRET_KEY` / `PANTHEON_ARTIFACT_BUCKET` posture requirement
  from `services/foundation/persistence_posture.py` and
  `services/source_search_posture.py` (`OBJECT_STORE_KEYS` /
  `object_store_keys`), since no consumer needs them enforced.
  `persona`/`registry`/`governance`/`memory`/`evaluation`/`optimizer-svc`/
  `postmortems`/`feedback` (the services in
  `storage-consumer-matrix.json`'s `depends_on_minio_healthy` lists) lose a
  dependency edge, not a capability.
- Delete the `minio` and `minio-init` service declarations and the
  `minio-data` volume from `docker-compose.yml` and
  `docker-compose.control.yml` only after this proof stands, after the
  hosted inventory check above finds nothing to preserve, and after the
  cutover task's own reversible local migration/readback check (per its
  acceptance criteria) confirms zero remaining source-code consumers — this
  task does not perform that deletion or that inventory check.
- Also update `scripts/bootstrap.sh`, which starts and depends on MinIO
  outside the two Compose files: line 66 lists `minio` in its
  `INFRA_SERVICES` array and brings it up via `docker compose up -d`, and
  line 118 runs `docker compose run --rm minio-init` to create the bucket.
  Both must be removed as part of the same retirement, not left to bit-rot
  after the Compose service declarations are deleted.
- If any later consumer genuinely needs true S3-only semantics (multipart
  upload from an external tool that cannot speak the GCS API, for example),
  that is a new decision with its own consumer/data enumeration, not a
  reason to keep an unmaintained, unused MinIO instance running today.

No paid hosting change, no real-data migration, and no MFA/capital
authorization is implied or requested by this choice; none is required for
the decision itself, since GCS is already the running managed backend for
all three real consumers. The hosted MinIO/GCS inventory check called out
above is a genuinely pending dependent action left for the cutover task,
not something this task fabricates as already cleared.
