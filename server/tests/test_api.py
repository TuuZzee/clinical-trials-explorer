from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.clinical_trials import RetrievalResult
from app.config import Settings
from app.errors import ServiceError
from app.main import app, get_clinical_trials, get_http_client, get_planner, get_settings
from app.planner import OpenAIPlanner
from app.schemas import QueryPlan, VisualizationRequest, VisualizationResponse
from tests.helpers.planner import output_response

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_services():
    original = dict(app.dependency_overrides)
    planner = SimpleNamespace(
        plan=AsyncMock(
            return_value=QueryPlan.model_validate(
                {
                    "intent": "distribution",
                    "measure": "study_count",
                    "filters": {},
                    "group_by": "phase",
                    "chart_type": "bar_chart",
                    "comparison": None,
                }
            )
        )
    )
    adapter = SimpleNamespace(
        search=AsyncMock(
            return_value=RetrievalResult(
                studies=[], total_matches=0, retrieved_records=0, pages_fetched=1
            )
        )
    )
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, openai_api_key=None)
    app.dependency_overrides[get_planner] = lambda: planner
    app.dependency_overrides[get_clinical_trials] = lambda: adapter
    yield planner, adapter
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def test_health_docs_and_openapi(isolated_services):
    assert client.get("/health").json() == {"status": "ok"}
    planner, adapter = isolated_services
    planner.plan.assert_not_awaited()
    adapter.search.assert_not_awaited()
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["components"]["schemas"]["VisualizationRequest"]["required"] == ["query"]
    errors = schema["paths"]["/api/v1/visualize"]["post"]["responses"]
    assert set(errors) == {"200", "422", "500", "501", "502", "503", "504"}
    assert all(
        errors[status]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
        for status in errors
        if status != "200"
    )


def test_valid_request_returns_empty_chart(isolated_services):
    planner, adapter = isolated_services
    response = client.post("/api/v1/visualize", json={"query": "Show phases of asthma trials"})
    assert response.status_code == 200
    assert response.json()["visualization"]["data"] == []
    assert VisualizationResponse.model_validate(response.json()).meta.complete_results
    planner.plan.assert_awaited_once()
    adapter.search.assert_awaited_once()
    assert VisualizationRequest(query="  phases  ").query == "phases"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 2001},
        {"query": 5},
        {"query": "q", "trial_status": "OPEN"},
        {"query": "q", "preferred_chart": "scatter_plot"},
        {"query": "q", "start_year": 1700},
        {"query": "q", "end_year": 2101},
        {"query": "q", "start_year": 2025, "end_year": 2020},
        {"query": "q", "start_year": True},
        {"query": "q", "start_year": "2020"},
        {"query": "q", "country": ""},
        {"query": "q", "unknown": "value"},
        {"query": "q", "drug_name": 'x" OR AREA[Condition]cancer'},
    ],
)
def test_invalid_requests(payload):
    response = client.post("/api/v1/visualize", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_settings_limits_and_secret_masking(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    settings = Settings(_env_file=None)
    assert "test-placeholder" not in repr(settings)
    for config in [
        {"ctgov_page_size": 1001},
        {"ctgov_max_retries": 6},
        {"ctgov_timeout_seconds": 0},
        {"ctgov_max_studies": 0},
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **config)


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent": "time_trend", "group_by": "start_year", "chart_type": "time_series"},
        {"intent": "geographic", "group_by": "country"},
        {"group_by": "overall_status"},
        {"group_by": "sponsor_class"},
        {"group_by": "intervention"},
        {
            "intent": "comparison",
            "chart_type": "grouped_bar_chart",
            "comparison": {"field": "condition", "values": ["asthma", "diabetes"]},
        },
    ],
)
def test_supported_plans_execute(isolated_services, overrides):
    planner, adapter = isolated_services
    data = planner.plan.return_value.model_dump(mode="json")
    planner.plan.return_value = QueryPlan.model_validate({**data, **overrides})
    response = client.post("/api/v1/visualize", json={"query": "A valid question"})
    assert response.status_code == 200
    assert response.json()["visualization"]["data"] == []
    assert adapter.search.await_count == (2 if planner.plan.return_value.comparison else 1)


@pytest.mark.parametrize(
    "code,status",
    [
        ("unsupported_query", 422),
        ("ambiguous_query", 422),
        ("conflicting_filters", 422),
        ("planner_refused", 422),
        ("planner_not_configured", 503),
        ("planner_timeout", 504),
        ("planner_unavailable", 503),
        ("planner_request_failed", 502),
        ("planner_invalid_response", 502),
    ],
)
def test_planner_errors_preserved(isolated_services, code, status):
    planner, adapter = isolated_services
    planner.plan.side_effect = ServiceError(code, "Safe planner error", status)
    response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": "Safe planner error"}}
    adapter.search.assert_not_awaited()


@pytest.mark.parametrize(
    "code,status",
    [
        ("upstream_unavailable", 503),
        ("upstream_invalid_response", 502),
        ("upstream_rejected_request", 502),
        ("incomplete_retrieval", 422),
        ("incomplete_retrieval", 502),
    ],
)
def test_registry_errors_preserved(isolated_services, code, status):
    _, adapter = isolated_services
    adapter.search.side_effect = ServiceError(code, "Safe registry error", status)
    response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "visualization" not in response.json()


def test_incomplete_adapter_result_cannot_become_chart(isolated_services):
    _, adapter = isolated_services
    adapter.search.return_value.complete_results = False
    response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "incomplete_retrieval"


@pytest.fixture
def wire_mock_upstreams():
    """Exercise the actual SDK planner + actual adapter with HTTP transports mocked."""

    def wire(registry_handler, model_plan=None, **settings_overrides):
        captured = []
        settings = Settings(
            _env_file=None,
            openai_api_key="unit-test-key",
            ctgov_max_retries=0,
            **settings_overrides,
        )
        plan = model_plan or {
            "intent": "distribution",
            "measure": "study_count",
            "filters": {"condition": "asthma"},
            "group_by": "phase",
            "chart_type": "bar_chart",
            "comparison": None,
        }

        def model_handler(request):
            captured.append(request)
            return httpx.Response(200, json=output_response(plan))

        async def planner_dependency():
            async with httpx.AsyncClient(transport=httpx.MockTransport(model_handler)) as http:
                async with AsyncOpenAI(api_key="unit-test-key", http_client=http) as sdk:
                    yield OpenAIPlanner(settings, sdk)

        async def http_dependency():
            async with httpx.AsyncClient(transport=httpx.MockTransport(registry_handler)) as http:
                yield http

        app.dependency_overrides.pop(get_clinical_trials)
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_planner] = planner_dependency
        app.dependency_overrides[get_http_client] = http_dependency
        return captured

    return wire


def test_real_components_filter_precedence_paging_and_metadata(
    wire_mock_upstreams, synthetic_study
):
    calls = []

    def registry_handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "studies": [synthetic_study],
                    "totalCount": 2,
                    "nextPageToken": "second-page",
                },
            )
        return httpx.Response(
            200,
            json={
                "studies": [{"protocolSection": {"identificationModule": {"nctId": "NCT00000002"}}}]
            },
        )

    model_calls = wire_mock_upstreams(registry_handler, ctgov_page_size=1)
    response = client.post(
        "/api/v1/visualize",
        json={
            "query": "Show phases of asthma trials",
            "condition": "diabetes",
            "country": "United States",
            "sponsor": "NCI",
            "trial_status": "COMPLETED",
            "start_year": 2015,
            "end_year": 2025,
        },
    )
    assert response.status_code == 200
    assert len(model_calls) == 1 and len(calls) == 2
    assert calls[0].url.params["query.cond"] == '"diabetes"'
    assert calls[0].url.params["query.spons"] == '"NCI"'
    assert calls[0].url.params["filter.overallStatus"] == "COMPLETED"
    assert calls[0].url.params["filter.advanced"] == (
        'AREA[LocationCountry]"United States" AND AREA[StartDate]RANGE[2015-01-01,2025-12-31]'
    )
    assert calls[1].url.params["pageToken"] == "second-page"
    output = VisualizationResponse.model_validate(response.json())
    assert output.meta.filters.condition == "diabetes"
    assert output.meta.retrieved_studies == output.meta.total_matches == 2
    assert output.meta.complete_results
    assert output.visualization.data == [
        {"phase": "Phase 2", "trial_count": 1},
        {"phase": "Missing phase", "trial_count": 1},
    ]


@pytest.mark.parametrize(
    "payload,settings,status,code",
    [
        ({"studies": [], "totalCount": 2}, {"ctgov_max_studies": 1}, 422, "incomplete_retrieval"),
        (
            {"studies": [], "nextPageToken": "more"},
            {"ctgov_max_pages": 1},
            422,
            "incomplete_retrieval",
        ),
        ({"studies": [], "totalCount": 1}, {}, 502, "incomplete_retrieval"),
        ({"studies": None}, {}, 502, "upstream_invalid_response"),
    ],
)
def test_actual_adapter_errors_through_endpoint(
    wire_mock_upstreams, payload, settings, status, code
):
    wire_mock_upstreams(lambda _: httpx.Response(200, json=payload), **settings)
    response = client.post("/api/v1/visualize", json={"query": "Show asthma trial phases"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_real_planner_missing_credentials_through_endpoint():
    app.dependency_overrides.pop(get_planner)
    response = client.post("/api/v1/visualize", json={"query": "Show trial phases"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "planner_not_configured"


@pytest.mark.parametrize(
    "grouping", ["phase", "overall_status", "sponsor_class", "intervention", "country"]
)
def test_comparison_searches_independently_and_counts_overlap(isolated_services, grouping):
    from app.clinical_trials import NormalizedStudy

    planner, adapter = isolated_services
    data = planner.plan.return_value.model_dump(mode="json")
    planner.plan.return_value = QueryPlan.model_validate(
        {
            **data,
            "intent": "comparison",
            "group_by": grouping,
            "chart_type": "grouped_bar_chart",
            "filters": {"country": "United States"},
            "comparison": {"field": "drug_name", "values": ["A", "B"]},
        }
    )
    shared = NormalizedStudy(nct_id="NCT00000001", phases=("PHASE2",))
    adapter.search.side_effect = [
        RetrievalResult(studies=[shared], total_matches=1, retrieved_records=1, pages_fetched=1),
        RetrievalResult(studies=[shared], total_matches=1, retrieved_records=1, pages_fetched=1),
    ]
    response = client.post("/api/v1/visualize", json={"query": "Comparison"})
    assert response.status_code == 200
    filters = [call.args[0] for call in adapter.search.await_args_list]
    assert [f.drug_name for f in filters] == ["A", "B"]
    assert all(f.country == "United States" for f in filters)
    assert response.json()["meta"]["retrieved_studies"] == 1
    assert response.json()["meta"]["overlap_studies"] == 1
    assert [c["retrieved_studies"] for c in response.json()["meta"]["cohorts"]] == [1, 1]
    planner.plan.assert_awaited_once()


@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("failure", ["upstream", "cap", "partial"])
def test_comparison_fails_as_a_whole(isolated_services, failed_index, failure):
    planner, adapter = isolated_services
    plan = planner.plan.return_value.model_dump(mode="json")
    planner.plan.return_value = QueryPlan.model_validate(
        {
            **plan,
            "intent": "comparison",
            "chart_type": "grouped_bar_chart",
            "comparison": {"field": "condition", "values": ["asthma", "diabetes"]},
        }
    )
    success = RetrievalResult(studies=[], total_matches=0, retrieved_records=0, pages_fetched=1)
    error = (
        ServiceError("upstream_unavailable", "Safe upstream failure", 503)
        if failure == "upstream"
        else ServiceError("incomplete_retrieval", "Cap exceeded", 422)
        if failure == "cap"
        else success.model_copy(update={"complete_results": False})
    )
    outcomes = [success, success]
    outcomes[failed_index] = error
    adapter.search.side_effect = outcomes
    response = client.post("/api/v1/visualize", json={"query": "Compare phases"})
    assert response.status_code == (
        503 if failure == "upstream" else 422 if failure == "cap" else 502
    )
    assert "visualization" not in response.json()
    assert adapter.search.await_count == failed_index + 1


def test_future_unimplemented_plan_fails_before_retrieval(isolated_services):
    planner, adapter = isolated_services
    planner.plan.return_value = SimpleNamespace(
        intent="relationship", measure="study_count", group_by="phase"
    )
    response = client.post("/api/v1/visualize", json={"query": "Future plan"})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"
    adapter.search.assert_not_awaited()


def test_histogram_full_pipeline_and_openapi(wire_mock_upstreams, synthetic_study):
    plan = {
        "intent": "distribution",
        "measure": "study_count",
        "filters": {"condition": "asthma"},
        "group_by": "enrollment_count",
        "chart_type": "histogram",
        "comparison": None,
    }
    calls = []

    def registry_handler(request):
        calls.append(request)
        return httpx.Response(200, json={"studies": [synthetic_study], "totalCount": 1})

    wire_mock_upstreams(registry_handler, model_plan=plan)
    response = client.post(
        "/api/v1/visualize",
        json={"query": "Show asthma trial enrollment size distribution", "condition": "diabetes"},
    )
    assert response.status_code == 200
    assert calls[0].url.params["query.cond"] == '"diabetes"'
    output = VisualizationResponse.model_validate(response.json())
    assert output.visualization.type == "histogram"
    assert sum(row.trial_count for row in output.visualization.data) == 1
    assert output.meta.histogram.actual_studies == 1
    schema = client.get("/openapi.json").json()
    union = schema["components"]["schemas"]["VisualizationResponse"]["properties"]["visualization"]
    assert "histogram" in union["discriminator"]["mapping"]


def test_network_full_pipeline_preserves_source_labels_and_openapi(
    wire_mock_upstreams, synthetic_study
):
    plan = {
        "intent": "relationship",
        "measure": "study_count",
        "filters": {"condition": "asthma"},
        "group_by": "sponsor_drug",
        "chart_type": "network_graph",
        "comparison": None,
    }
    calls = []

    def registry_handler(request):
        calls.append(request)
        return httpx.Response(200, json={"studies": [synthetic_study], "totalCount": 1})

    wire_mock_upstreams(registry_handler, model_plan=plan)
    response = client.post(
        "/api/v1/visualize",
        json={
            "query": "Show lead sponsor drug networks for asthma trials",
            "condition": "diabetes",
            "preferred_chart": "network_graph",
        },
    )
    assert response.status_code == 200
    assert calls[0].url.params["query.cond"] == '"diabetes"'
    output = VisualizationResponse.model_validate(response.json())
    nodes = output.visualization.data.nodes
    assert {node.label for node in nodes if node.entity_type == "sponsor"} == {
        synthetic_study["protocolSection"]["sponsorCollaboratorsModule"]["leadSponsor"]["name"]
    }
    assert {node.label for node in nodes if node.entity_type == "drug"} == {
        intervention["name"]
        for intervention in synthetic_study["protocolSection"]["armsInterventionsModule"][
            "interventions"
        ]
        if intervention["type"] == "DRUG"
    }
    assert output.meta.graph.included_studies == 1
    assert all(edge.weight == 1 for edge in output.visualization.data.edges)
    schema = client.get("/openapi.json").json()
    union = schema["components"]["schemas"]["VisualizationResponse"]["properties"]["visualization"]
    assert "network_graph" in union["discriminator"]["mapping"]
    assert set(schema["components"]["schemas"]["GraphNode"]["properties"]) == {
        "id",
        "label",
        "entity_type",
    }


@pytest.mark.parametrize(
    "chart_type,grouping,intent",
    [
        ("histogram", "enrollment_count", "distribution"),
        ("network_graph", "sponsor_drug", "relationship"),
    ],
)
def test_histogram_and_network_require_complete_population(
    isolated_services, chart_type, grouping, intent
):
    planner, adapter = isolated_services
    planner.plan.return_value = QueryPlan.model_validate(
        {
            "intent": intent,
            "measure": "study_count",
            "group_by": grouping,
            "chart_type": chart_type,
            "filters": {},
            "comparison": None,
        }
    )
    adapter.search.side_effect = ServiceError("incomplete_retrieval", "Cap exceeded", 422)
    response = client.post("/api/v1/visualize", json={"query": "A supported question"})
    assert response.status_code == 422 and "visualization" not in response.json()
