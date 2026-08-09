# Evidence Narrative: OPS-AI-STATUS-SENTINEL-TIMESTAMP-OVERFLOW-20260809

## Overview
During assistant dev bridge materialization, converting maximum UTC sentinel timestamp (`9999-12-31T23:59:59Z`) into `Asia/Taipei` (UTC+8) in `format_display_timestamp` raised `OverflowError: date value out of range`. This occurred inside `refresh_derived_status_views` while formatting activity log entries, causing derived view generation to crash after canonical task mutations had committed.

## Root Cause Analysis
- `parse_timestamp("9999-12-31T23:59:59Z")` parses into `datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)`.
- Converting this UTC timestamp to `ZoneInfo("Asia/Taipei")` adds 8 hours, yielding year 10000.
- Python `datetime` instances cannot exceed year 9999 (`datetime.MAXYEAR`), raising `OverflowError`.

## Implementation & Fix
1. In `scripts/ai_status.py`, `format_display_timestamp` now wraps `.astimezone(DISPLAY_TIMEZONE).strftime(...)` in a `try...except (OverflowError, ValueError)` block.
2. On overflow during timezone conversion, it gracefully returns the raw string value (or `parsed.isoformat()`).
3. Added `TestSentinelTimestampOverflow` to `scripts/test_ai_status.py` validating:
   - Sentinel max UTC timestamp (`9999-12-31T23:59:59Z`) formatting fallback.
   - Max UTC `datetime` object formatting fallback.
   - Normal UTC timestamp localization to `Asia/Taipei`.
   - Malformed timestamp and `None` fallbacks.
   - Embedded timestamp localization (`localize_embedded_timestamps`).
   - Derived view refresh with sentinel timestamps in activity logs (`write_current_work`).

## Verification
- Executed `scripts/test_ai_status.py` unit test suite (201 tests passed in 35.10s).
- Executed `git diff --check` (clean, exit code 0).
