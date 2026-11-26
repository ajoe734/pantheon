"""Test-only, complete historical Source codecs; no Git or network at test time.

Fixtures are verbatim ``git show <full commit>:<SOURCE_PATH>`` outputs. The
commit/path/blob bindings were verified against Git when vendoring; runtime
checks verify the complete bytes before compiling either historical codec.
Only requirement_state is loaded, never a service entrypoint or provider.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import types


SOURCE_PATH = "services/source_ingestion/requirement_state.py"
BEE_REF = "bee62bc2978618539984aff9db136b064adc14a0"
NEW70A_REF = "70a5bb860e855beacd14445c29bff64ccfa7e8f2"
# Full source commit -> (fixture, byte length, Git blob SHA1, content SHA256).
CODECS = {
    BEE_REF: ("requirement_state_bee62bc.py", 43183,
              "5c52524ec58fb555b7af101a495bce825fc10595", "f0f68702dfef8c3ecc9e7345ea547d15c6574853a417bc6d1d5017bf3f33617c"),
    NEW70A_REF: ("requirement_state_70a5bb8.py", 57615,
                 "16f31f4e38bcd39e10d2a3a8e82a96b18fa59f44", "358258b3a4f67ba0b243e6b499fabb60342cabd572b6d98e6cd10b6bc9f6dbfe"),
}


def frozen_source_codec(ref: str) -> types.ModuleType:
    """Verify and execute the exact codec, failing closed on missing/tampered data."""
    if ref not in CODECS:
        raise ValueError("unknown frozen Source codec commit")
    filename, size, blob, digest = CODECS[ref]
    path = Path(__file__).with_name("fixtures") / filename
    raw = path.read_bytes()
    actual_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if len(raw) != size or actual_blob != blob or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("frozen Source codec integrity mismatch: " + ref)
    name = "services.source_ingestion._frozen_" + ref
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
