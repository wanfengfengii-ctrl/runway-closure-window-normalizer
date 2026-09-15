"""FastAPI application exposing the runway closure planning endpoint."""

from __future__ import annotations

from fastapi import FastAPI

from .errors import ApiValidationError, register_error_handlers
from .models import PlanRequest, PlanResponse, WindowOut
from .service import compute_windows, format_utc_minute, parse_and_validate

app = FastAPI(
    title="Runway Closure Planner",
    version="1.0.0",
    description=(
        "Merges overlapping or touching runway closure windows submitted by "
        "contractor groups and returns the complementary available windows "
        "inside a query range."
    ),
)
register_error_handlers(app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/runway-windows", response_model=PlanResponse, status_code=200)
def plan_runway_windows(payload: PlanRequest) -> PlanResponse:
    parsed, details = parse_and_validate(payload)
    if details:
        # Any invalid item rejects the whole request; nothing partial leaks.
        raise ApiValidationError(details)
    assert parsed is not None

    merged, available = compute_windows(parsed.query_start, parsed.query_end, parsed.closures)
    return PlanResponse(
        runway=parsed.runway,
        query=WindowOut(
            start=format_utc_minute(parsed.query_start),
            end=format_utc_minute(parsed.query_end),
        ),
        closures=[
            WindowOut(start=format_utc_minute(start), end=format_utc_minute(end))
            for start, end in merged
        ],
        available=[
            WindowOut(start=format_utc_minute(start), end=format_utc_minute(end))
            for start, end in available
        ],
    )
