from __future__ import annotations

import pytest

from services.trade_journey.telemetry_rows import decode_event_payload


def test_accepts_mapping_and_object_text() -> None:
    assert decode_event_payload({"a": 1}) == {"a": 1}
    assert decode_event_payload('{"a": 1}') == {"a": 1}


@pytest.mark.parametrize("value", ["{bad", "[1]", '"text"', "null", None, 5, [1]])
def test_rejects_malformed_or_non_object(value: object) -> None:
    with pytest.raises(ValueError):
        decode_event_payload(value)
