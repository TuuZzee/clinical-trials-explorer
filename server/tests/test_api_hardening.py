import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

import app.main as api
from app.api_examples import REQUEST_EXAMPLES
from app.clinical_trials import ClinicalTrialsClient, RetrievalResult
from app.config import Settings
from app.planner import OpenAIPlanner
from app.schemas import QueryPlan, VisualizationRequest, VisualizationResponse
from tests.helpers.planner import output_response


def phase_plan(**changes):
    return QueryPlan.model_validate(
        {
            "intent": "distribution",
            "measure": "study_count",
            "filters": {},
            "group_by": "phase",
            "chart_type": "bar_chart",
            "comparison": None,
            **changes,
        }
    )


def empty_result():
    return RetrievalResult(studies=[], total_matches=0, retrieved_records=0, pages_fetched=1)


@pytest.fixture
def services():
    original = dict(api.app.dependency_overrides)
    planner = SimpleNamespace(plan=AsyncMock(return_value=phase_plan()))
    adapter = SimpleNamespace(search=AsyncMock(return_value=empty_result()))
    settings = Settings(_env_file=None, openai_api_key=None, request_timeout_seconds=1)
    api.app.dependency_overrides.update(
        {
            api.get_planner: lambda: planner,
            api.get_clinical_trials: lambda: adapter,
            api.get_settings: lambda: settings,
        }
    )
    yield planner, adapter, settings
    api.app.dependency_overrides.clear()
    api.app.dependency_overrides.update(original)


def test_named_openapi_examples_validate_and_preserve_pattern():
    with TestClient(api.app) as client:
        schema = client.get("/openapi.json").json()
    examples = schema["paths"]["/api/v1/visualize"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert examples == REQUEST_EXAMPLES
    assert set(examples) == {
        "phase_distribution",
        "annual_trends",
        "two_drug_comparison",
        "enrollment_histogram",
        "sponsor_drug_network",
    }
    for example in examples.values():
        request = VisualizationRequest.model_validate(example["value"])
        assert request.query and example["summary"] and example["description"]
        assert all(value not in ("string", "A", "B") for value in example["value"].values())
    pattern = schema["components"]["schemas"]["VisualizationRequest"]["properties"]["drug_name"][
        "anyOf"
    ][0]["pattern"]
    assert pattern == r'^[^"\\\[\]{}\x00-\x1f\x7f]+$'


@pytest.mark.parametrize(
    "variable,value",
    [
        ("CTGOV_PAGE_SIZE", "0"),
        ("OPENAI_MODEL", ""),
        ("REQUEST_TIMEOUT_SECONDS", "-1"),
        ("OPENAI_TIMEOUT_SECONDS", "not-a-number"),
    ],
)
def test_invalid_environment_configuration_is_safe_at_http_boundary(
    services, monkeypatch, variable, value
):
    api.app.dependency_overrides.pop(api.get_settings)
    monkeypatch.setenv(variable, value)
    monkeypatch.setenv("OPENAI_API_KEY", "private-credential-marker")
    with TestClient(api.app) as client:
        response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "invalid_configuration"
    assert "private-credential-marker" not in response.text
    assert variable not in response.text and "Traceback" not in response.text


def test_configuration_file_error_is_sanitized_and_health_remains_available(services, monkeypatch):
    api.app.dependency_overrides.pop(api.get_settings)

    def fail_settings():
        raise OSError("private filesystem diagnostic private-credential-marker")

    monkeypatch.setattr(api, "Settings", fail_settings)
    with TestClient(api.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert (
        response.status_code == 503 and response.json()["error"]["code"] == "invalid_configuration"
    )
    assert "private" not in response.text


@pytest.mark.parametrize(
    "body",
    ["{bad json", "[]", "null", '"private-credential-marker"', '{"query":false}', '{"query":""}'],
)
def test_malformed_bodies_are_consistent_and_not_reflected(services, body):
    with TestClient(api.app) as client:
        response = client.post(
            "/api/v1/visualize",
            content=body,
            headers={"Content-Type": "application/json", "X-Private": "private-marker"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "private" not in response.text
    services[0].plan.assert_not_awaited()


def test_total_deadline_cancels_planning_before_registry(services):
    planner, adapter, settings = services
    settings.request_timeout_seconds = 0.01
    cancelled = threading.Event()

    async def slow_plan(_):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.set()

    planner.plan.side_effect = slow_plan
    with TestClient(api.app) as client:
        response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == 504 and response.json()["error"]["code"] == "request_timeout"
    assert cancelled.is_set()
    adapter.search.assert_not_awaited()


def test_total_deadline_cancels_second_cohort_without_partial_chart(services):
    planner, adapter, settings = services
    settings.request_timeout_seconds = 0.02
    planner.plan.return_value = phase_plan(
        intent="comparison",
        chart_type="grouped_bar_chart",
        comparison={"field": "drug_name", "values": ["A", "B"]},
    )
    cancelled = threading.Event()
    calls = []

    async def retrieve(filters):
        calls.append(filters)
        if len(calls) == 1:
            return empty_result()
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.set()

    adapter.search.side_effect = retrieve
    with TestClient(api.app) as client:
        response = client.post("/api/v1/visualize", json={"query": "Comparison"})
    assert response.status_code == 504 and response.json()["error"]["code"] == "request_timeout"
    assert len(calls) == 2 and [f.drug_name for f in calls] == ["A", "B"]
    assert cancelled.is_set() and "visualization" not in response.json()


def test_aggregation_and_serialization_are_offloaded_and_deadline_can_stop_waiting(
    services, monkeypatch
):
    services[2].request_timeout_seconds = 0.02
    worker_started = threading.Event()
    release_worker = threading.Event()
    caller_thread = threading.get_ident()
    worker_threads = []
    original = api._render_response

    def slow_render(plan, results):
        worker_threads.append(threading.get_ident())
        worker_started.set()
        release_worker.wait(timeout=1)
        return original(plan, results)

    monkeypatch.setattr(api, "_render_response", slow_render)
    try:
        with TestClient(api.app) as client:
            response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
            assert response.status_code == 504
            assert worker_started.is_set() and worker_threads[0] != caller_thread
            release_worker.set()
    finally:
        release_worker.set()


def test_external_task_cancellation_propagates_to_inflight_request(services):
    planner, adapter, _ = services

    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def waiting(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        planner.plan.side_effect = waiting
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as http:
            task = asyncio.create_task(
                http.post("/api/v1/visualize", json={"query": "Show trial phases"})
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert cancelled.is_set()
        adapter.search.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    "target,status,code",
    [
        ("model_rate_limit", 503, "planner_unavailable"),
        ("model_timeout", 504, "planner_timeout"),
        ("model_invalid", 502, "planner_invalid_response"),
        ("registry_timeout", 503, "upstream_unavailable"),
        ("registry_invalid", 502, "upstream_invalid_response"),
    ],
)
def test_real_sdk_and_adapter_errors_remain_distinct_and_sanitized(services, target, status, code):
    settings = Settings(_env_file=None, openai_api_key="unit-test-key", ctgov_max_retries=0)

    def model_handler(request):
        if target == "model_rate_limit":
            return httpx.Response(429, json={"error": {"message": "private-upstream-body"}})
        if target == "model_timeout":
            raise httpx.ReadTimeout("private transport diagnostic", request=request)
        return httpx.Response(
            200,
            json=output_response(text="private-invalid-json")
            if target == "model_invalid"
            else output_response(phase_plan().model_dump(mode="json")),
        )

    def registry_handler(request):
        if target == "registry_timeout":
            raise httpx.ReadTimeout("private registry diagnostic", request=request)
        return httpx.Response(200, json={"studies": None})

    async def planner_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(model_handler)) as http:
            async with AsyncOpenAI(api_key="unit-test-key", http_client=http) as sdk:
                yield OpenAIPlanner(settings, sdk)

    async def registry_dependency():
        async with httpx.AsyncClient(transport=httpx.MockTransport(registry_handler)) as http:
            yield ClinicalTrialsClient(http, settings)

    api.app.dependency_overrides[api.get_planner] = planner_dependency
    api.app.dependency_overrides[api.get_clinical_trials] = registry_dependency
    with TestClient(api.app) as client:
        response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert "private" not in response.text and "unit-test-key" not in response.text
    assert "visualization" not in response.json()


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"intent": "time_trend", "group_by": "start_year", "chart_type": "time_series"},
        {
            "intent": "comparison",
            "chart_type": "grouped_bar_chart",
            "comparison": {"field": "condition", "values": ["A", "B"]},
        },
        {"group_by": "enrollment_count", "chart_type": "histogram"},
        {"intent": "relationship", "group_by": "sponsor_drug", "chart_type": "network_graph"},
    ],
)
def test_every_chart_family_has_valid_empty_result(services, changes):
    services[0].plan.return_value = phase_plan(**changes)
    with TestClient(api.app) as client:
        response = client.post("/api/v1/visualize", json={"query": "A supported question"})
    assert response.status_code == 200
    output = VisualizationResponse.model_validate_json(response.content)
    assert output.meta.retrieved_studies == 0 and output.meta.complete_results


@pytest.mark.parametrize("invalid", ["wrong_type", "mutated_rows"])
def test_invalid_final_chart_never_bypasses_response_validation(services, monkeypatch, invalid):
    original = api.build_chart

    def broken_chart(plan, results):
        if invalid == "wrong_type":
            return {"private": "private-upstream-marker"}
        output = original(plan, results)
        output.visualization.data = [{"phase": "private-upstream-marker", "trial_count": True}]
        return output

    monkeypatch.setattr(api, "build_chart", broken_chart)
    with TestClient(api.app) as client:
        response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "invalid_visualization"
    assert "private" not in response.text and "visualization" not in response.json()
