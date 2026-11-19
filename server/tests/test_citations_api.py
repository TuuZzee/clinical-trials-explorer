"""HTTP opt-in boundaries and unchanged ordinary response bytes."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.clinical_trials import ClinicalTrialsClient
from app.config import Settings
from app.models import CitedVisualizationResponse, QueryPlan, VisualizationRequest
from app.provenance import EvidenceBudget
from tests.helpers.planner import output_response, plan_data, run_planner


def api_replies(monkeypatch, source, payloads, **configuration):
    async def run():
        settings = Settings(_env_file=None, **configuration)
        requests = []

        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"studies": [source], "totalCount": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as upstream:
            planner = SimpleNamespace(
                plan=AsyncMock(return_value=QueryPlan.model_validate(plan_data()))
            )
            adapter = ClinicalTrialsClient(upstream, settings)
            monkeypatch.setattr(
                main.app,
                "dependency_overrides",
                {
                    main.get_settings: lambda: settings,
                    main.get_planner: lambda: planner,
                    main.get_clinical_trials: lambda: adapter,
                },
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(main.app), base_url="http://test"
            ) as http:
                replies = [
                    await http.post("/api/v1/visualize", json=payload) for payload in payloads
                ]
        return replies, requests

    return asyncio.run(run())


def test_omitted_false_and_enabled_have_identical_visualizations(monkeypatch, synthetic_study):
    replies, requests = api_replies(
        monkeypatch,
        synthetic_study,
        [
            {"query": "Show phases"},
            {"query": "Show phases", "include_citations": False},
            {"query": "Show phases", "include_citations": True},
        ],
    )
    assert all(reply.status_code == 200 for reply in replies)
    assert replies[0].content == replies[1].content
    assert "citations" not in replies[0].json()
    cited = CitedVisualizationResponse.model_validate_json(replies[2].content)
    assert cited.model_dump(mode="json", exclude={"citations"}) == replies[0].json()
    assert len(requests) == 3  # One registry search per request; no per-study re-fetches.
    assert all("include_citations" not in request.url.params for request in requests)
    assert cited.citations.datums[0].studies[0].nct_id == "NCT99990001"


def test_disabled_capture_and_limits_have_no_effect(monkeypatch, synthetic_study):
    import app.clinical_trials as registry

    def forbidden(*args, **kwargs):
        pytest.fail("Ordinary requests must not capture source evidence")

    monkeypatch.setattr(registry, "capture_study", forbidden)
    replies, _ = api_replies(
        monkeypatch,
        synthetic_study,
        [{"query": "Show phases"}],
        citation_max_bytes=1,
        citation_max_references=1,
    )
    assert replies[0].status_code == 200 and "citations" not in replies[0].json()


def test_byte_limit_uses_established_error_contract(monkeypatch, synthetic_study):
    replies, _ = api_replies(
        monkeypatch,
        synthetic_study,
        [{"query": "Show phases", "include_citations": True}],
        citation_max_bytes=1,
    )
    assert replies[0].status_code == 422
    assert replies[0].json() == {
        "error": {
            "code": "citation_limit_exceeded",
            "message": "Citation limit exceeded. Narrow the filters or disable citations.",
        }
    }


def test_reference_limit_fails_whole_chart_instead_of_sampling(monkeypatch, synthetic_study):
    source = deepcopy(synthetic_study)
    source["protocolSection"]["designModule"]["phases"] = ["PHASE1", "PHASE2"]
    replies, _ = api_replies(
        monkeypatch,
        source,
        [{"query": "Show phases", "include_citations": True}],
        citation_max_references=1,
    )
    assert replies[0].status_code == 422
    assert set(replies[0].json()) == {"error"}
    assert replies[0].json()["error"]["code"] == "citation_limit_exceeded"


def test_citation_switch_is_not_part_of_the_model_input_or_plan():
    import json

    request = VisualizationRequest(query="Show trial phases", include_citations=True)
    result, calls = run_planner(output_response(plan_data()), request)
    body = json.loads(calls[0].content)
    assert body["input"] == request.query
    assert "include_citations" not in calls[0].content.decode()
    assert "include_citations" not in result.model_dump()


def test_corrupted_capture_is_sanitized_at_the_http_boundary(monkeypatch, synthetic_study):
    import app.clinical_trials as registry

    original = registry.capture_study

    def corrupt(*args, **kwargs):
        record = original(*args, **kwargs)
        record.fields[1].value = "private-inconsistent-source"
        return record

    monkeypatch.setattr(registry, "capture_study", corrupt)
    replies, _ = api_replies(
        monkeypatch, synthetic_study, [{"query": "Show phases", "include_citations": True}]
    )
    assert replies[0].status_code == 500
    assert replies[0].json()["error"]["code"] == "invalid_citations"
    assert "private-inconsistent-source" not in replies[0].text


def test_shared_source_budget_applies_across_cohorts(synthetic_study):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"studies": [synthetic_study], "totalCount": 1}
                )
            )
        ) as http:
            from app.models.enums import Grouping

            adapter = ClinicalTrialsClient(http, Settings(_env_file=None))
            budget = EvidenceBudget(1_000_000)
            await adapter.search(
                VisualizationRequest(query="Show phases"),
                evidence_grouping=Grouping.PHASE,
                evidence_budget=budget,
            )
            initial = budget.captured_bytes
            budget.max_bytes = initial
            from app.errors import ServiceError

            with pytest.raises(ServiceError) as exc:
                await adapter.search(
                    VisualizationRequest(query="Show phases"),
                    evidence_grouping=Grouping.PHASE,
                    evidence_budget=budget,
                )
            assert exc.value.code == "citation_limit_exceeded"

    asyncio.run(run())


def test_citations_share_the_overall_execution_deadline(monkeypatch, synthetic_study):
    import time

    original = main._render_cited_response

    def slow(*args):
        time.sleep(0.05)
        return original(*args)

    monkeypatch.setattr(main, "_render_cited_response", slow)
    replies, _ = api_replies(
        monkeypatch,
        synthetic_study,
        [{"query": "Show phases", "include_citations": True}],
        request_timeout_seconds=0.01,
    )
    assert replies[0].status_code == 504
    assert replies[0].json()["error"]["code"] == "request_timeout"


def test_citation_debug_events_do_not_include_source_excerpts(monkeypatch, synthetic_study, caplog):
    import logging

    from app.debug import logger

    caplog.set_level(logging.DEBUG, logger=logger.name)
    source = deepcopy(synthetic_study)
    source["protocolSection"]["designModule"]["phases"] = ["private-raw-source-sentinel"]
    replies, _ = api_replies(
        monkeypatch, source, [{"query": "Show phases", "include_citations": True}]
    )
    assert replies[0].status_code == 200
    assert any(getattr(record, "event", None) == "citations.complete" for record in caplog.records)
    assert "private-raw-source-sentinel" not in caplog.text
