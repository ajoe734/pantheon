# AG-GAP-007 — Agora capabilities mismatch and dev journal cleanup

Status: completed

## 1. Capabilities Endpoint Fix

### Bug Description
A relative path traversal issue was identified in `services/control-plane/bff/agora/router.py` for the definition of `_CAPABILITY_MANIFEST_PATH`. 
The path incorrectly traversed up 3 parent directories instead of 2 (resolving to `services/specs/agora/capability_manifest.json` instead of `services/control-plane/specs/agora/capability_manifest.json`). This caused file loading to fail silently under `_load_capability_manifest()` and return an empty `capabilities` array, while `/me` continued to successfully return all 7 granted capabilities from the token claims.

### Fix
Corrected the traversal depth in `services/control-plane/bff/agora/router.py`:
```python
_CAPABILITY_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "specs", "agora", "capability_manifest.json",
)
```

## 2. Contract Verification Tests
Updated `test_agora_capabilities_returns_manifest` in `services/control-plane/bff/tests/test_agora_router.py` to explicitly assert that the returned capability list is non-empty and correctly projected:
```python
    capabilities_list = body["data"]["capabilities"]
    assert len(capabilities_list) == 7, f"Expected 7 capabilities, got {len(capabilities_list)}"
    cap_names = {c["name"] for c in capabilities_list}
    assert "agora.identity.v1" in cap_names
    assert "agora.session.v1" in cap_names
```
All BFF tests pass successfully (`pytest services/control-plane/bff/tests/test_agora_router.py` -> `18 passed`).

## 3. Dev Journal Cleanup

### Residue Identified
Three dry-run write-probe records were found under the key `decision_journal_entries` in the active container database file `/data/bff/read_surfaces.json` inside `pantheon-operator-bff-1`:
- `journal-agora-f92c72620c` (title: "dev-probe")
- `journal-agora-6787b45923` (title: "dev-probe")
- `journal-agora-9e9d02cc19` (title: "dry-run-write-probe-...")

### Cleanup Operations Procedure
To purge the dry-run residues without altering legitimate production records, the following command was executed on the running `pantheon-operator-bff-1` container:

```bash
docker exec pantheon-operator-bff-1 python3 -c "
import json
path = '/data/bff/read_surfaces.json'
data = json.load(open(path))
before = len(data['decision_journal_entries'])
data['decision_journal_entries'] = {
    k: v for k, v in data['decision_journal_entries'].items()
    if not ('probe' in v.get('title', '') or 'dry-run' in v.get('title', '') or 'probe' in v.get('body', '') or 'dry-run' in v.get('body', ''))
}
json.dump(data, open(path, 'w'), indent=2)
print('Cleaned decision_journal_entries in ' + path + '. Count: ' + str(before) + ' -> ' + str(len(data['decision_journal_entries'])))
"
```

**Verification:** The cleanup successfully reduced the record count from 3 to 0. A query to check the database keys confirmed they were purged:
```bash
docker exec pantheon-operator-bff-1 python3 -c 'import json; data=json.load(open("/data/bff/read_surfaces.json")); print(list(data["decision_journal_entries"].keys()))'
# Output: []
```
