"""End-to-end API tests through FastAPI's TestClient."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

URL = "/api/v1/runway-windows"

QUERY = {"start": "2026-09-15T22:00:00Z", "end": "2026-09-16T06:00:00Z"}

BASE_PAYLOAD = {"query": QUERY, "runway": "09L", "closures": []}


def payload(**overrides):
    body = copy.deepcopy(BASE_PAYLOAD)
    body.update(overrides)
    return body


def error_details(response):
    body = response.json()
    assert set(body.keys()) == {"error"}, "error responses must not leak partial results"
    assert body["error"]["code"] == "VALIDATION_ERROR"
    return {(d["code"], d["path"]) for d in body["error"]["details"]}


class TestHappyPath:
    def test_merge_clip_and_complement(self):
        response = client.post(
            URL,
            json=payload(
                closures=[
                    {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T01:00:00Z"},
                    {"start": "2026-09-16T00:30:00Z", "end": "2026-09-16T02:00:00Z"},
                    {"start": "2026-09-16T02:00:00Z", "end": "2026-09-16T03:00:00Z"},
                    {"start": "2026-09-15T20:00:00Z", "end": "2026-09-15T22:30:00Z"},
                    {"start": "2026-09-16T05:30:00Z", "end": "2026-09-16T08:00:00Z"},
                    {"start": "2026-09-16T10:00:00Z", "end": "2026-09-16T11:00:00Z"},
                ]
            ),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["runway"] == "09L"
        assert body["query"] == QUERY
        assert body["closures"] == [
            {"start": "2026-09-15T22:00:00Z", "end": "2026-09-15T22:30:00Z"},
            {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T03:00:00Z"},
            {"start": "2026-09-16T05:30:00Z", "end": "2026-09-16T06:00:00Z"},
        ]
        assert body["available"] == [
            {"start": "2026-09-15T22:30:00Z", "end": "2026-09-15T23:00:00Z"},
            {"start": "2026-09-16T03:00:00Z", "end": "2026-09-16T05:30:00Z"},
        ]

    def test_no_closures_returns_exact_query_interval(self):
        response = client.post(URL, json=payload())
        assert response.status_code == 200
        body = response.json()
        assert body["closures"] == []
        assert body["available"] == [QUERY]

    def test_full_coverage_returns_empty_available(self):
        response = client.post(
            URL,
            json=payload(
                closures=[
                    {"start": "2026-09-15T22:00:00Z", "end": "2026-09-16T00:00:00Z"},
                    {"start": "2026-09-16T00:00:00Z", "end": "2026-09-16T06:00:00Z"},
                ]
            ),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["closures"] == [QUERY]
        assert body["available"] == []

    def test_results_sorted_by_start(self):
        response = client.post(
            URL,
            json=payload(
                closures=[
                    {"start": "2026-09-16T04:00:00Z", "end": "2026-09-16T05:00:00Z"},
                    {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T00:00:00Z"},
                ]
            ),
        )
        assert response.status_code == 200
        starts = [w["start"] for w in response.json()["closures"]]
        assert starts == sorted(starts)

    def test_runways_are_planned_independently(self):
        closures = [{"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T00:00:00Z"}]
        first = client.post(URL, json=payload(runway="09L", closures=closures))
        second = client.post(URL, json=payload(runway="27R", closures=closures))
        assert first.status_code == second.status_code == 200
        assert first.json()["runway"] == "09L"
        assert second.json()["runway"] == "27R"
        # Stateless: each response reflects only its own request.
        assert first.json()["closures"] == second.json()["closures"] == closures

    def test_health_endpoint(self):
        assert client.get("/health").json() == {"status": "ok"}


class TestTimeValidation:
    @pytest.mark.parametrize(
        "bad_start",
        [
            "2026-09-15T23:00:30Z",  # seconds must be 00
            "2026-09-15T23:00Z",  # seconds required
            "2026-09-15T23:00:00+08:00",  # UTC only
            "2026-09-15T23:00:00",  # zone designator required
            "2026-02-30T23:00:00Z",  # impossible date
            "not-a-time",
        ],
    )
    def test_invalid_closure_time_is_422(self, bad_start: str):
        response = client.post(
            URL,
            json=payload(closures=[{"start": bad_start, "end": "2026-09-16T00:00:00Z"}]),
        )
        assert response.status_code == 422
        assert ("INVALID_TIME_FORMAT", "closures[0].start") in error_details(response)

    def test_invalid_query_time_path(self):
        response = client.post(
            URL, json=payload(query={"start": "oops", "end": QUERY["end"]})
        )
        assert response.status_code == 422
        assert ("INVALID_TIME_FORMAT", "query.start") in error_details(response)


class TestRangeValidation:
    @pytest.mark.parametrize(
        "query",
        [
            {"start": "2026-09-16T06:00:00Z", "end": "2026-09-16T06:00:00Z"},  # equal
            {"start": "2026-09-16T07:00:00Z", "end": "2026-09-16T06:00:00Z"},  # reversed
        ],
    )
    def test_query_start_must_precede_end(self, query):
        response = client.post(URL, json=payload(query=query))
        assert response.status_code == 422
        assert ("INVALID_RANGE", "query") in error_details(response)

    def test_closure_start_must_precede_end(self):
        response = client.post(
            URL,
            json=payload(
                closures=[{"start": "2026-09-16T01:00:00Z", "end": "2026-09-16T01:00:00Z"}]
            ),
        )
        assert response.status_code == 422
        assert ("INVALID_RANGE", "closures[0]") in error_details(response)

    def test_one_bad_item_rejects_whole_request_and_reports_all(self):
        response = client.post(
            URL,
            json=payload(
                closures=[
                    {"start": "2026-09-15T23:00:00Z", "end": "2026-09-16T00:00:00Z"},  # valid
                    {"start": "2026-09-16T02:00:00Z", "end": "2026-09-16T01:00:00Z"},  # reversed
                    {"start": "bad", "end": "2026-09-16T03:00:00Z"},  # bad format
                ]
            ),
        )
        assert response.status_code == 422
        details = error_details(response)
        assert ("INVALID_RANGE", "closures[1]") in details
        assert ("INVALID_TIME_FORMAT", "closures[2].start") in details


class TestStructuralValidation:
    def test_missing_runway(self):
        body = payload()
        del body["runway"]
        response = client.post(URL, json=body)
        assert response.status_code == 422
        assert ("MISSING_FIELD", "runway") in error_details(response)

    def test_missing_closures(self):
        body = payload()
        del body["closures"]
        response = client.post(URL, json=body)
        assert response.status_code == 422
        assert ("MISSING_FIELD", "closures") in error_details(response)

    def test_closures_must_be_a_list(self):
        response = client.post(URL, json=payload(closures="nope"))
        assert response.status_code == 422
        assert ("INVALID_TYPE", "closures") in error_details(response)

    def test_closure_item_must_be_an_object(self):
        response = client.post(URL, json=payload(closures=[42]))
        assert response.status_code == 422
        assert ("INVALID_TYPE", "closures[0]") in error_details(response)

    def test_extra_field_rejected(self):
        response = client.post(URL, json=payload(contractor="acme"))
        assert response.status_code == 422
        assert ("UNEXPECTED_FIELD", "contractor") in error_details(response)

    def test_empty_runway_rejected(self):
        response = client.post(URL, json=payload(runway="   "))
        assert response.status_code == 422
        assert ("INVALID_VALUE", "runway") in error_details(response)

    def test_malformed_json_is_422(self):
        response = client.post(
            URL, content="{not json", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert ("INVALID_JSON", "$") in error_details(response)

    def test_unknown_route_has_stable_code(self):
        response = client.get("/no/such/route")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
