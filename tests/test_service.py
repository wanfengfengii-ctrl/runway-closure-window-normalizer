"""Unit tests for time handling and the merge/complement algorithm."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.service import compute_windows, format_utc_minute, parse_utc_minute


def dt(value: str) -> datetime:
    moment = parse_utc_minute(value)
    assert moment is not None, f"test fixture {value!r} must be a valid timestamp"
    return moment


class TestParseUtcMinute:
    def test_valid_whole_minute(self):
        assert parse_utc_minute("2026-09-15T22:30:00Z") == datetime(
            2026, 9, 15, 22, 30, tzinfo=timezone.utc
        )

    def test_midnight_and_leap_day(self):
        assert parse_utc_minute("2028-02-29T00:00:00Z") is not None

    @pytest.mark.parametrize(
        "value",
        [
            "2026-09-15T22:30:15Z",  # non-zero seconds
            "2026-09-15T22:30Z",  # missing seconds
            "2026-09-15T22:30:00+08:00",  # non-UTC offset
            "2026-09-15T22:30:00",  # missing zone designator
            "2026-09-15 22:30:00Z",  # space instead of T
            "2026-02-30T22:00:00Z",  # impossible date
            "2026-13-01T22:00:00Z",  # month out of range
            "2026-09-15T24:00:00Z",  # hour out of range
            "2026-09-15T22:60:00Z",  # minute out of range
            "0000-01-01T00:00:00Z",  # year zero
            "not-a-time",
            "",
        ],
    )
    def test_rejects_invalid_forms(self, value: str):
        assert parse_utc_minute(value) is None

    def test_format_roundtrip(self):
        moment = dt("2026-09-15T22:30:00Z")
        assert format_utc_minute(moment) == "2026-09-15T22:30:00Z"


class TestComputeWindows:
    QUERY_START = dt("2026-09-15T22:00:00Z")
    QUERY_END = dt("2026-09-16T06:00:00Z")

    def run(self, closures):
        return compute_windows(self.QUERY_START, self.QUERY_END, closures)

    def test_empty_closures_returns_full_query_interval(self):
        merged, available = self.run([])
        assert merged == []
        assert available == [(self.QUERY_START, self.QUERY_END)]

    def test_single_closure_inside_range(self):
        merged, available = self.run([(dt("2026-09-15T23:00:00Z"), dt("2026-09-16T01:00:00Z"))])
        assert merged == [(dt("2026-09-15T23:00:00Z"), dt("2026-09-16T01:00:00Z"))]
        assert available == [
            (dt("2026-09-15T22:00:00Z"), dt("2026-09-15T23:00:00Z")),
            (dt("2026-09-16T01:00:00Z"), dt("2026-09-16T06:00:00Z")),
        ]

    def test_overlapping_closures_merge(self):
        merged, _ = self.run(
            [
                (dt("2026-09-15T23:00:00Z"), dt("2026-09-16T01:00:00Z")),
                (dt("2026-09-16T00:30:00Z"), dt("2026-09-16T02:00:00Z")),
            ]
        )
        assert merged == [(dt("2026-09-15T23:00:00Z"), dt("2026-09-16T02:00:00Z"))]

    def test_touching_closures_merge(self):
        merged, available = self.run(
            [
                (dt("2026-09-15T22:00:00Z"), dt("2026-09-16T00:00:00Z")),
                (dt("2026-09-16T00:00:00Z"), dt("2026-09-16T06:00:00Z")),
            ]
        )
        assert merged == [(self.QUERY_START, self.QUERY_END)]
        assert available == []

    def test_contained_closure_is_absorbed(self):
        merged, _ = self.run(
            [
                (dt("2026-09-15T23:00:00Z"), dt("2026-09-16T05:00:00Z")),
                (dt("2026-09-16T00:00:00Z"), dt("2026-09-16T01:00:00Z")),
            ]
        )
        assert merged == [(dt("2026-09-15T23:00:00Z"), dt("2026-09-16T05:00:00Z"))]

    def test_closures_are_clipped_to_query_range(self):
        merged, available = self.run(
            [
                (dt("2026-09-15T20:00:00Z"), dt("2026-09-15T22:30:00Z")),
                (dt("2026-09-16T05:30:00Z"), dt("2026-09-16T08:00:00Z")),
            ]
        )
        assert merged == [
            (dt("2026-09-15T22:00:00Z"), dt("2026-09-15T22:30:00Z")),
            (dt("2026-09-16T05:30:00Z"), dt("2026-09-16T06:00:00Z")),
        ]
        assert available == [(dt("2026-09-15T22:30:00Z"), dt("2026-09-16T05:30:00Z"))]

    def test_closures_outside_range_are_discarded(self):
        merged, available = self.run(
            [
                (dt("2026-09-15T10:00:00Z"), dt("2026-09-15T11:00:00Z")),
                (dt("2026-09-16T10:00:00Z"), dt("2026-09-16T11:00:00Z")),
                # Touching the boundary only: empty intersection, discarded.
                (dt("2026-09-15T21:00:00Z"), dt("2026-09-15T22:00:00Z")),
                (dt("2026-09-16T06:00:00Z"), dt("2026-09-16T07:00:00Z")),
            ]
        )
        assert merged == []
        assert available == [(self.QUERY_START, self.QUERY_END)]

    def test_unsorted_input_is_sorted_and_merged(self):
        merged, _ = self.run(
            [
                (dt("2026-09-16T04:00:00Z"), dt("2026-09-16T05:00:00Z")),
                (dt("2026-09-15T23:00:00Z"), dt("2026-09-16T00:00:00Z")),
                (dt("2026-09-16T01:00:00Z"), dt("2026-09-16T02:00:00Z")),
            ]
        )
        starts = [start for start, _ in merged]
        assert starts == sorted(starts)
        assert merged == [
            (dt("2026-09-15T23:00:00Z"), dt("2026-09-16T00:00:00Z")),
            (dt("2026-09-16T01:00:00Z"), dt("2026-09-16T02:00:00Z")),
            (dt("2026-09-16T04:00:00Z"), dt("2026-09-16T05:00:00Z")),
        ]

    def test_full_coverage_leaves_no_availability(self):
        merged, available = self.run([(dt("2026-09-15T21:00:00Z"), dt("2026-09-16T07:00:00Z"))])
        assert merged == [(self.QUERY_START, self.QUERY_END)]
        assert available == []

    def test_duplicate_closures_merge(self):
        window = (dt("2026-09-15T23:00:00Z"), dt("2026-09-16T00:00:00Z"))
        merged, _ = self.run([window, window])
        assert merged == [window]

    def test_closure_spanning_midnight_chain(self):
        merged, available = self.run(
            [
                (dt("2026-09-15T22:30:00Z"), dt("2026-09-15T23:30:00Z")),
                (dt("2026-09-15T23:00:00Z"), dt("2026-09-16T00:30:00Z")),
                (dt("2026-09-16T00:00:00Z"), dt("2026-09-16T01:00:00Z")),
            ]
        )
        assert merged == [(dt("2026-09-15T22:30:00Z"), dt("2026-09-16T01:00:00Z"))]
        assert available == [
            (dt("2026-09-15T22:00:00Z"), dt("2026-09-15T22:30:00Z")),
            (dt("2026-09-16T01:00:00Z"), dt("2026-09-16T06:00:00Z")),
        ]
