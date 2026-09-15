"""Request and response schemas for the planning endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WindowIn(BaseModel):
    """A half-open [start, end) window submitted by a contractor group."""

    model_config = ConfigDict(extra="forbid")

    start: str
    end: str


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: WindowIn
    runway: str
    closures: list[WindowIn]


class WindowOut(BaseModel):
    start: str
    end: str


class PlanResponse(BaseModel):
    runway: str
    query: WindowOut
    closures: list[WindowOut]
    available: list[WindowOut]
