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

Two independent findings, both read-only, both reproducible from this repo:

1. **MinIO/S3 has zero real code consumers.** `docker-compose.yml` and
   `docker-compose.control.yml` each declare a `minio` server + `minio-init`
   bootstrap and wire `PANTHEON_S3_ENDPOINT` / `PANTHEON_S3_ACCESS_KEY` /
   `PANTHEON_S3_SECRET_KEY` / `PANTHEON_ARTIFACT_BUCKET` into 17 (main stack)
   / 12 (control stack) application services. A repo-wide, non-test search
   for `boto3`, `botocore`, or any S3 client construction under `services/`
   and `scripts/` returns nothing. The only two files that read those four
   env vars — `services/foundation/persistence_posture.py` and
   `services/source_search_posture.py` — check only that the strings are
   non-empty when the staging/prod posture is enforced; by their own
   docstrings, "It does not open database or object-store connections."
   No object has ever been written to or read from MinIO by product code.
2. **The one real object-store consumer already uses a managed backend, not
   MinIO.** `services/control-plane/bff/management_ai_store.py`
   (`ManagementAiAttachmentStore`) uploads/downloads management-AI chat
   attachments through `google-cloud-storage` (`storage.Client().bucket(...)
   .blob(...)`), gated by `PANTHEON_MGMT_AI_ATTACH_BUCKET`. When that bucket
   env var is unset (the default in both compose files today) it falls back
   to local filesystem storage, never to MinIO. This code path already has
   an isolated round-trip proof:
   `services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py::
   test_management_ai_attachment_store_uses_gcs_bucket_metadata`.

No service declares retention, legal-hold, versioning, server-side
encryption, or per-tenant bucket/prefix isolation for either backend; the
existing partition scheme is a `session_id/turn_id` path prefix inside one
shared bucket. There is no MinIO-specific backup/restore runbook — MinIO
objects fall under the same generic VM-disk-snapshot policy as every other
stateful backend (`docs/deployment/vm-dev-staging-prod-management-plan.md`
§11.3), not a bucket-level restore procedure.

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
for an S3 API that literally nothing in this codebase calls. There is no
S3 semantics to prove for a consumer that does not exist, and it does
nothing to help the one real object-store consumer, which already uses
GCS.

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
storage" as a concept: zero services perform S3 I/O against it, and the
declared `minio-data` volume holds nothing but the empty bucket
`minio-init` creates at boot. Retirement here is not "delete object
storage" — the one real workload (BFF attachments) keeps its already-chosen
GCS/local-fallback path unchanged. This option is folded into the chosen
disposition below rather than treated as a separate branch, since it only
concerns a component that already has zero consumers.

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
production, satisfying every S3-shaped semantic that any current consumer
actually needs (there is no consumer needing multipart upload, versioning,
or legal-hold today per §1). The repository also carries a pre-existing
equivalent test,
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

Migration/restore feasibility: there is no data to migrate (§1 — the MinIO
bucket is empty of real objects), and restore feasibility for the chosen
GCS path is inherited from GCS's own managed durability/versioning
features, which the cutover task can enable per-bucket if a future
consumer needs it.

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
proven in isolation) is the one accepted object-store backend for Pantheon
going forward. The self-hosted MinIO/S3 layer is accepted for retirement
because it has zero real consumers and zero real data today — not because
object storage in general is being removed.

**Exact scope for the later cutover task (`OSS-OBJECT-STORE-CUTOVER-001`):**
- Reuse `ManagementAiAttachmentStore` / the `google-cloud-storage` client
  as the sole object-store adapter path; do not introduce a second storage
  abstraction.
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
  `docker-compose.control.yml` only after this proof stands and after the
  cutover task's own reversible local migration/readback check (per its
  acceptance criteria) confirms zero remaining consumers — this task does
  not perform that deletion.
- If any later consumer genuinely needs true S3-only semantics (multipart
  upload from an external tool that cannot speak the GCS API, for example),
  that is a new decision with its own consumer/data enumeration, not a
  reason to keep an unmaintained, unused MinIO instance running today.

No paid hosting change, no real-data migration, and no MFA/capital
authorization is implied or requested by this choice; none is required,
since GCS is already the running managed backend for the one real
consumer.
