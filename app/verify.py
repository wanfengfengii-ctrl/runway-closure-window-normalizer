"""One-shot acceptance checks against a running API instance.

Used by the ``verify`` docker-compose service.  Exits 0 when every check
passes and 1 otherwise.  Target the API with ``API_BASE_URL`` (defaults to
the compose service name ``http://api:8000``).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000").rstrip("/")
PLAN_URL = f"{BASE_URL}/api/v1/runway-windows"

QUERY = {"start": "2026-09-15T22:00:00Z", "end": "2026-09-16T06:00:00Z"}

_failures: list[str] = []


def check(name: str, condition: bool, context: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f"  -- {context}" if context and not condition else ""))
    if not condition:
        _failures.append(name)


def post(payload: Any) -> httpx.Response:
    return httpx.post(PLAN_URL, json=payload, timeout=10.0)


def wait_for_api(attempts: int = 60) -> bool:
    for _ in range(attempts):
        try:
            if httpx.get(f"{BASE_URL}/health", timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def expect_plan(
    name: str,
    payload: dict[str, Any],
    expected_closures: list[dict[str, str]],
    expected_available: list[dict[str, str]],
) -> None:
    try:
        resp = post(payload)
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - report any failure as a failed check
        check(name, False, f"request failed: {exc}")
        return
    ok = (
        resp.status_code == 200
        and body.get("closures") == expected_closures
        and body.get("available") == expected_available
    )
    check(name, ok, f"status={resp.status_code} body={resp.text[:400]}")


def expect_error(name: str, payload: Any, expected_details: set[tuple[str, str]]) -> None:
    try:
        resp = post(payload)
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"request failed: {exc}")
        return
    details = body.get("error", {}).get("details", []) if isinstance(body, dict) else []
    got = {(d.get("code"), d.get("path")) for d in details}
    ok = (
        resp.status_code == 422
        and body.get("error", {}).get("code") == "VALIDATION_ERROR"
        and expected_details <= got
        # All-or-nothing: an invalid request must not leak partial results.
        and "closures" not in body
        and "available" not in body
    )
    check(name, ok, f"status={resp.status_code} body={resp.text[:400]}")


def main() -> int:
    if not wait_for_api():
        print(f"API at {BASE_URL} did not become healthy in time.")
        return 1

    # 1. Overlap + touching + clipping + out-of-range discard, all at once.
    expect_plan(
        "merge overlapping/touching closures and clip to query range",
        {
            "query": QUERY,
            "runway": "09L",
            "closures": [
                {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T01:00:00Z"},
                {"start": "2026-09-16T00:30:00Z", "end": "2026-09-16T02:00:00Z"},
                {"start": "2026-09-16T02:00:00Z", "end": "2026-09-16T03:00:00Z"},
                {"start": "2026-09-15T20:00:00Z", "end": "2026-09-15T22:30:00Z"},
                {"start": "2026-09-16T05:30:00Z", "end": "2026-09-16T08:00:00Z"},
                {"start": "2026-09-16T10:00:00Z", "end": "2026-09-16T11:00:00Z"},
            ],
        },
        expected_closures=[
            {"start": "2026-09-15T22:00:00Z", "end": "2026-09-15T22:30:00Z"},
            {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T03:00:00Z"},
            {"start": "2026-09-16T05:30:00Z", "end": "2026-09-16T06:00:00Z"},
        ],
        expected_available=[
            {"start": "2026-09-15T22:30:00Z", "end": "2026-09-15T23:00:00Z"},
            {"start": "2026-09-16T03:00:00Z", "end": "2026-09-16T05:30:00Z"},
        ],
    )

    # 2. No closures -> exactly the full query interval is available.
    expect_plan(
        "no closures returns the full query interval",
        {"query": QUERY, "runway": "27R", "closures": []},
        expected_closures=[],
        expected_available=[QUERY],
    )

    # 3. Closures covering the whole range -> empty available list.
    expect_plan(
        "full coverage yields empty available list",
        {
            "query": QUERY,
            "runway": "09L",
            "closures": [
                {"start": "2026-09-15T22:00:00Z", "end": "2026-09-16T00:00:00Z"},
                {"start": "2026-09-16T00:00:00Z", "end": "2026-09-16T06:00:00Z"},
            ],
        },
        expected_closures=[{"start": "2026-09-15T22:00:00Z", "end": "2026-09-16T06:00:00Z"}],
        expected_available=[],
    )

    # 4. Runway isolation: same closures on another runway never mix.
    expect_plan(
        "different runways are planned independently",
        {
            "query": QUERY,
            "runway": "18",
            "closures": [{"start": "2026-09-15T23:00:00Z", "end": "2026-09-15T23:30:00Z"}],
        },
        expected_closures=[{"start": "2026-09-15T23:00:00Z", "end": "2026-09-15T23:30:00Z"}],
        expected_available=[
            {"start": "2026-09-15T22:00:00Z", "end": "2026-09-15T23:00:00Z"},
            {"start": "2026-09-15T23:30:00Z", "end": "2026-09-16T06:00:00Z"},
        ],
    )

    # 5. Invalid timestamps (seconds, offset zone, impossible date).
    expect_error(
        "non-minute seconds are rejected",
        {"query": QUERY, "runway": "09L", "closures": [{"start": "2026-09-15T23:00:30Z", "end": "2026-09-16T00:00:00Z"}]},
        {("INVALID_TIME_FORMAT", "closures[0].start")},
    )
    expect_error(
        "non-UTC offset is rejected",
        {"query": {"start": "2026-09-15T22:00:00+08:00", "end": QUERY["end"]}, "runway": "09L", "closures": []},
        {("INVALID_TIME_FORMAT", "query.start")},
    )
    expect_error(
        "impossible calendar date is rejected",
        {"query": QUERY, "runway": "09L", "closures": [{"start": "2026-02-30T23:00:00Z", "end": "2026-09-16T00:00:00Z"}]},
        {("INVALID_TIME_FORMAT", "closures[0].start")},
    )

    # 6. Range rules, including aggregation of several bad items at once.
    expect_error(
        "query start must precede query end",
        {"query": {"start": QUERY["end"], "end": QUERY["start"]}, "runway": "09L", "closures": []},
        {("INVALID_RANGE", "query")},
    )
    expect_error(
        "every invalid closure item is reported in one 422",
        {
            "query": QUERY,
            "runway": "09L",
            "closures": [
                {"start": "2026-09-16T01:00:00Z", "end": "2026-09-15T23:00:00Z"},
                {"start": "not-a-time", "end": "2026-09-16T00:00:00Z"},
            ],
        },
        {("INVALID_RANGE", "closures[0]"), ("INVALID_TIME_FORMAT", "closures[1].start")},
    )

    # 7. Structural problems: missing field, wrong type, unknown field.
    expect_error(
        "missing runway is rejected",
        {"query": QUERY, "closures": []},
        {("MISSING_FIELD", "runway")},
    )
    expect_error(
        "closures must be an array",
        {"query": QUERY, "runway": "09L", "closures": "nope"},
        {("INVALID_TYPE", "closures")},
    )
    expect_error(
        "unknown fields are rejected",
        {"query": QUERY, "runway": "09L", "closures": [], "contractor": "acme"},
        {("UNEXPECTED_FIELD", "contractor")},
    )

    if _failures:
        print(f"\n{len(_failures)} acceptance check(s) FAILED.")
        return 1
    print("\nAll acceptance checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
