"""Single source of the OpenClaw model chain.

The ordered chain (first entry = primary) comes from the deployment variable
``OPENCLAW_MODEL_CHAIN`` (comma/whitespace separated model refs). Only when the
variable is unset or blank does the one-line default file
``model-chain.default`` (next to this module) apply. No other source file names
a model as part of a chain or an allowed-model list.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional

CHAIN_ENV = "OPENCLAW_MODEL_CHAIN"
DEFAULT_CHAIN_FILE = Path(__file__).with_name("model-chain.default")
GATEWAY_NATIVE_RUNTIME = "openclaw"


def parse_chain(raw: Any) -> List[str]:
    """Ordered, de-duplicated refs from a delimited string or an iterable."""
    if raw is None:
        return []
    items: Iterable[Any] = re.split(r"[,\s]+", raw) if isinstance(raw, str) else raw
    out: List[str] = []
    for item in items:
        ref = str(item or "").strip()
        if ref and ref not in out:
            out.append(ref)
    return out


def default_chain() -> List[str]:
    try:
        return parse_chain(DEFAULT_CHAIN_FILE.read_text(encoding="utf-8"))
    except OSError:
        return []


def configured_chain(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    env = os.environ if environ is None else environ
    return parse_chain(env.get(CHAIN_ENV)) or default_chain()


def chain_from_gateway_model(model: Any) -> List[str]:
    """Chain from a gateway ``agents.defaults.model`` value (str or primary/fallbacks)."""
    if isinstance(model, str):
        return parse_chain(model)
    if isinstance(model, Mapping):
        return parse_chain([model.get("primary"), *(model.get("fallbacks") or [])])
    return []


def registered_refs(models: Any) -> List[str]:
    """Model refs registered in the gateway (``agents.defaults.models`` keys)."""
    return [str(k) for k in models] if isinstance(models, Mapping) else []


def cli_runtime_refs(models: Any) -> set[str]:
    """Registered refs whose agentRuntime is not the gateway-native runtime.

    Persona tool calls lack host identity on CLI backends, so these refs may not
    appear in a chain or a persona fallback.
    """
    out: set[str] = set()
    if not isinstance(models, Mapping):
        return out
    for ref, entry in models.items():
        runtime = entry.get("agentRuntime") if isinstance(entry, Mapping) else None
        rid = str(runtime.get("id") or "").strip() if isinstance(runtime, Mapping) else ""
        if rid and rid != GATEWAY_NATIVE_RUNTIME:
            out.add(str(ref))
    return out
