"""Local development-task bridge, deliberately outside the product BFF."""
import json
from typing import Any, Mapping


def canonical_packet_bytes(packet: Mapping[str, Any]) -> bytes:
    """Encode original signed wire content without models or new defaults."""
    data = dict(packet)
    data.pop("signature", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
