"""Core planning logic: time parsing, business validation, window merging.

All intervals are half-open [start, end).  A window therefore occupies its
start instant but not its end instant, so two windows whose endpoints touch
(``a.end == b.start``) cover a continuous span and must be merged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import INVALID_RANGE, INVALID_TIME_FORMAT, INVALID_VALUE, make_detail
from .models import PlanRequest

# UTC RFC3339 whole-minute form: seconds are required and must be "00",
# and the only accepted zone designator is "Z".
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

MAX_RUNWAY_LENGTH = 64


def parse_utc_minute(value: str) -> datetime | None:
    """Parse a UTC RFC3339 whole-minute timestamp; return None when invalid."""
    if not TIME_PATTERN.match(value):
        return None
    try:
        parsed = datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def format_utc_minute(moment: datetime) -> str:
    """Render an aware datetime in the canonical UTC whole-minute form."""
    return moment.astimezone(timezone.utc).strftime(TIME_FORMAT)


@dataclass(frozen=True)
class ParsedRequest:
    runway: str
    query_start: datetime
    query_end: datetime
    closures: list[tuple[datetime, datetime]]


def _parse_time_field(value: str, path: str, details: list[dict[str, str]]) -> datetime | None:
    moment = parse_utc_minute(value)
    if moment is None:
        details.append(
            make_detail(
                INVALID_TIME_FORMAT,
                path,
                "Expected a UTC RFC3339 whole-minute timestamp like 2026-09-15T22:30:00Z "
                "(seconds must be 00, zone designator must be Z).",
            )
        )
    return moment


def parse_and_validate(payload: PlanRequest) -> tuple[ParsedRequest | None, list[dict[str, str]]]:
    """Validate business rules, collecting every violation at once.

    Returns ``(parsed, [])`` on success or ``(None, details)`` when any rule
    is broken — the caller must then reject the whole request.
    """
    details: list[dict[str, str]] = []

    runway = payload.runway.strip()
    if not runway:
        details.append(make_detail(INVALID_VALUE, "runway", "Runway identifier must not be empty."))
    elif len(runway) > MAX_RUNWAY_LENGTH:
        details.append(
            make_detail(
                INVALID_VALUE,
                "runway",
                f"Runway identifier must be at most {MAX_RUNWAY_LENGTH} characters.",
            )
        )

    query_start = _parse_time_field(payload.query.start, "query.start", details)
    query_end = _parse_time_field(payload.query.end, "query.end", details)
    if query_start is not None and query_end is not None and query_start >= query_end:
        details.append(
            make_detail(INVALID_RANGE, "query", "Query start must be earlier than query end.")
        )

    closures: list[tuple[datetime, datetime]] = []
    for index, window in enumerate(payload.closures):
        start = _parse_time_field(window.start, f"closures[{index}].start", details)
        end = _parse_time_field(window.end, f"closures[{index}].end", details)
        if start is None or end is None:
            continue
        if start >= end:
            details.append(
                make_detail(
                    INVALID_RANGE,
                    f"closures[{index}]",
                    "Closure start must be earlier than closure end.",
                )
            )
            continue
        closures.append((start, end))

    if details:
        return None, details
    assert query_start is not None and query_end is not None
    return ParsedRequest(runway, query_start, query_end, closures), []


def compute_windows(
    query_start: datetime,
    query_end: datetime,
    closures: list[tuple[datetime, datetime]],
) -> tuple[list[tuple[datetime, datetime]], list[tuple[datetime, datetime]]]:
    """Clip closures to the query range, merge them, and derive the complement.

    Steps:
      1. Clip every closure to [query_start, query_end) and drop windows that
         do not intersect it.
      2. Merge windows that overlap or touch (end of one == start of the next),
         sorted by start time.
      3. The available windows are the gaps of the merged closures inside the
         query range.

    Merging only ever happens within the closures of a single request, so
    windows of different runways are never combined.
    """
    clipped: list[tuple[datetime, datetime]] = []
    for start, end in closures:
        lo = max(start, query_start)
        hi = min(end, query_end)
        if lo < hi:
            clipped.append((lo, hi))

    clipped.sort()
    merged: list[list[datetime]] = []
    for start, end in clipped:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])

    available: list[tuple[datetime, datetime]] = []
    cursor = query_start
    for start, end in merged:
        if cursor < start:
            available.append((cursor, start))
        cursor = end
    if cursor < query_end:
        available.append((cursor, query_end))

    return [(window[0], window[1]) for window in merged], available
