# Pantheon Execution Grant Issuer — Deployment Guide

OPS-EXECUTION-MFA-ISSUER-001.
Source of record: `ISSUER-SA-SD-20260908.md`.

## 1. Security Architecture & Boundary

The Execution Grant Issuer is a small development-tooling service that verifies genuine Google Cloud Identity Platform user ID tokens with completed second-factor (MFA) claims against an explicit operator UID allowlist. Once verified, it signs an exact task-bound execution authorization grant conforming to `execution_authorization.py`.

### Critical Security Boundaries
- **Isolated Authority:** The service runs under its own system identity (`pantheon-issuer`) outside worker authority. Workers must NOT have sudo rights or write permissions to the issuer's key or configuration.
- **Dedicated Signing Key:** The service signs grants using an Ed25519 private key generated specifically for execution authorization. The private key never leaves the issuer host.
- **No Client Policy Substitutions:** Challenges bind the full canonical task policy snapshot. The issuer rejects any client-selected policy changes at issue time.
- **Strict S5 / Step 5 Pause:** The service strictly rejects tasks associated with Step 5 / S5.
- **Redacted Audit Receipts:** Audit logs record only non-sensitive metadata (`task_id`, `generation`, `actor_uid`, `nonce`, `policy_digest`). No raw ID tokens, bearer secrets, or private keys are ever printed or committed.

## 2. Deployment Instructions

### Step 2.1: Key Generation
On the dedicated issuer host (or secure enclave):
```bash
python3 deploy/execution-grant-issuer/run_server.py \
  --generate-key-pair /etc/pantheon/execution-grant-issuer/ed25519-private.pem \
  --key-id pantheon-mfa-issuer-dev-20260908
```
This generates the private key with permissions `0600` and outputs the public key base64url trust string and fingerprint.

### Step 2.2: Configure Application Default Credentials
Token verification (`identity_platform.check_revocation` included) is performed
by the pinned `firebase-admin` SDK, authenticated with Application Default
Credentials on this host -- never a downloadable service-account key file.
Provision ADC once per issuer host, e.g.:
```bash
gcloud auth application-default login --project=pantheon-dev-20260902
```
or attach a workload identity / metadata-server credential if the issuer
runs on GCE/GKE. `GET /healthz` fails closed (503) if ADC cannot actually be
resolved; see Section 8 of the operations guide.

### Step 2.3: Configure Service
Copy `issuer-config.example.json` to `/etc/pantheon/execution-grant-issuer/config.json` and configure:
1. `identity_platform.project_id`: Target identity project (default: `pantheon-dev-20260902`).
2. `identity_platform.allowed_operator_uids`: Allowlist of human operator UIDs permitted to authorize execution.
3. `signing.private_key_file`: Path to the generated Ed25519 private key.
4. `signing.key_id`: Matching key ID.

### Step 2.4: Configure Systemd Service
```bash
sudo cp deploy/execution-grant-issuer/pantheon-execution-grant-issuer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pantheon-execution-grant-issuer
sudo systemctl start pantheon-execution-grant-issuer
sudo systemctl status pantheon-execution-grant-issuer
```

### Step 2.5: Configure Public Trust in Pantheon
Record the issuer public key into `.orchestrator/config.json`:
```json
{
  "execution_authorization": {
    "mfa_issuer_public_keys": {
      "pantheon-mfa-issuer-dev-20260908": "<base64url-public-key>"
    }
  }
}
```
**Command-Runtime Immutability & Supervisor Promotion:**
`PANTHEON_COMMAND_ROOT` is an immutable runtime whose `scripts/ai_status.py:311` loads its own committed `.orchestrator/config.json` (`CONFIG_FILE = ROOT / ".orchestrator" / "config.json"`). Simply committing the key to the repository or updating the status root does not update what `scripts/ai-status.sh execution-grant-submit` trusts.

Publishing or rotating issuer keys requires materializing a new immutable command runtime under `$DEPLOY_ROOT/command-runtimes/<TARGET_SHA>` and promoting it via `scripts/promote_supervisor_runtime.py`:
1. Run `--discover-only --json` to validate candidate invariants and live config.
2. Run `--promote` to replace the supervisor runtime and update `PANTHEON_COMMAND_ROOT`.
3. Verify **both** effective key fingerprints:
   - The active signer fingerprint from the issuer service (`--inspect-key` or startup log).
   - The promoted runtime config fingerprint from `$PANTHEON_COMMAND_ROOT/.orchestrator/config.json`.
4. For rollback or key revocation, follow this exact same qualified promotion path (promoting a new runtime with the key removed, or re-promoting a prior known-good command runtime). Never mutate or patch immutable runtimes in place.

See full operational procedure in `docs/operations/execution-grant-issuer.md` § 6.3 and § 6.4.

## 3. Health & Verification Probes
- Health endpoint: `curl -s http://127.0.0.1:8090/healthz`
- Tooling UI: `http://127.0.0.1:8090/tooling`
