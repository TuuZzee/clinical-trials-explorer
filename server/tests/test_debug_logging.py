"""DEBUG observability with real logging configuration and deterministic upstreams."""

import asyncio
import json
import logging
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.clinical_trials import ClinicalTrialsClient, compile_filters
from app.config import Settings
from app.debug import MAX_TEXT, debug, logger, request_id
from app.errors import ServiceError
from app.models import QueryPlan, StudyFilters
from tests.helpers.planner import output_response, plan_data, run_planner

FILTERS = StudyFilters(
    condition="breast cancer",
    drug_name="pembrolizumab",
    sponsor="Acme",
    country="France",
    trial_status="COMPLETED",
    start_year=2015,
    end_year=2025,
)


def raw_study(index):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": f"NCT{index:08d}"},
            "designModule": {"phases": ["PHASE2"]},
        },
        "private_body": "raw-registry-response-sentinel",
    }


def registry(handler, filters=FILTERS):
    async def run():
        delays = []

        async def sleep(delay):
            delays.append(delay)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer private-header-sentinel"},
        ) as http:
            adapter = ClinicalTrialsClient(
                http,
                Settings(
                    _env_file=None,
                    openai_api_key="private-key-sentinel",
                    ctgov_page_size=1,
                    ctgov_max_retries=1,
                ),
                sleep,
            )
            return await adapter.search(filters), delays

    return asyncio.run(run())


def events(caplog, event):
    return [
        record.details
        for record in caplog.records
        if record.name == logger.name and getattr(record, "event", None) == event
    ]


def test_registry_debug_parameters_paging_retry_and_completion(caplog):
    caplog.set_level(logging.DEBUG, logger=logger.name)
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "99"}, text="private-body-sentinel"),
            httpx.Response(
                200,
                json={
                    "studies": [raw_study(1)],
                    "totalCount": 2,
                    "nextPageToken": "opaque-pagination-sentinel",
                },
            ),
            httpx.Response(200, json={"studies": [raw_study(2)], "totalCount": 2}),
        ]
    )
    result, delays = registry(lambda request: next(responses))
    assert result.complete_results and len(result.studies) == 2 and delays == [5]
    requests = events(caplog, "ctgov.request")
    assert [(r["page"], r["attempt"], r["token_present"]) for r in requests] == [
        (1, 1, False),
        (1, 2, False),
        (2, 1, True),
    ]
    assert all(r["params"] == {**compile_filters(FILTERS), "pageSize": 1} for r in requests)
    assert all(r["method"] == "GET" and r["path"].endswith("/api/v2/studies") for r in requests)
    successful = [r for r in events(caplog, "ctgov.response") if r["status"] == 200]
    assert [(r["studies"], r["total"], r["next_page"]) for r in successful] == [
        (1, 2, True),
        (1, 2, False),
    ]
    assert all(r["duration_ms"] >= 0 for r in events(caplog, "ctgov.response"))
    assert events(caplog, "ctgov.retry")[0]["backoff_seconds"] == 5
    summary = events(caplog, "ctgov.complete")[0]
    assert (summary["pages"], summary["retrieved"], summary["distinct"]) == (2, 2, 2)
    for secret in (
        "private-key-sentinel",
        "private-header-sentinel",
        "private-body-sentinel",
        "raw-registry-response-sentinel",
        "opaque-pagination-sentinel",
    ):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "failure,code",
    [
        (400, "upstream_rejected_request"),
        (503, "upstream_unavailable"),
        ("timeout", "upstream_unavailable"),
        ("malformed", "upstream_invalid_response"),
    ],
)
def test_registry_failures_are_sanitized_and_keep_error_mapping(caplog, failure, code):
    caplog.set_level(logging.DEBUG, logger=logger.name)
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-exception-sentinel")
        if failure == "malformed":
            return httpx.Response(200, text="private-response-sentinel")
        return httpx.Response(failure, text="private-response-sentinel")

    with pytest.raises(ServiceError) as exc:
        registry(handler)
    assert exc.value.code == code
    assert events(caplog, "ctgov.failed")[-1]["category"] == code
    assert len(calls) == (2 if failure in (503, "timeout") else 1)
    assert "private-" not in caplog.text


def test_log_text_is_bounded_control_safe_and_key_patterns_are_redacted(caplog):
    caplog.set_level(logging.DEBUG, logger=logger.name)
    debug(
        "test.safe",
        value="before\n\x1b\u202e" + "x" * 500,
        accidental_key="sk-proj-fake-key",
    )
    details = events(caplog, "test.safe")[0]
    assert len(details["value"]) == MAX_TEXT + 3
    assert details["value"].startswith("before???")
    assert "fake-key" not in caplog.text
    assert details["accidental_key"] == "[redacted]"


def test_info_suppresses_application_debug_and_search_terms(caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    registry(lambda request: httpx.Response(200, json={"studies": []}))
    assert not [record for record in caplog.records if record.name == logger.name]
    assert "breast cancer" not in caplog.text and "pembrolizumab" not in caplog.text


def test_broken_logging_handler_cannot_change_registry_result(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger=logger.name)

    def broken(*args, **kwargs):
        raise OSError("Broken logging stream")

    monkeypatch.setattr(logger, "debug", broken)
    result, _ = registry(lambda request: httpx.Response(200, json={"studies": []}))
    assert result.complete_results and result.studies == []


@pytest.mark.parametrize("level,visible", [("debug", True), ("info", False)])
def test_uvicorn_log_level_controls_application_logger_without_global_changes(level, visible):
    code = f"""
import logging
from uvicorn import Config
from app.debug import debug,logger
root_level = logging.getLogger().level
httpx_level = logging.getLogger("httpx").level
Config("app.main:app",log_level={level!r},use_colors=False).configure_logging()
debug("configuration.probe",value="visible-only-at-debug")
assert logging.getLogger().level == root_level
assert logging.getLogger("httpx").level == httpx_level
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert ("configuration.probe" in result.stderr) is visible


def test_planner_decisions_and_errors_do_not_expose_model_text(caplog):
    caplog.set_level(logging.DEBUG, logger=logger.name)
    plan, _ = run_planner(output_response(plan_data()))
    assert plan.group_by.value == "phase"
    assert events(caplog, "planner.decision")[-1]["decision"] == "supported"
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(refusal=True))
    assert exc.value.code == "planner_refused"
    assert events(caplog, "planner.failed")[-1]["category"] == "planner_refused"
    assert "Show trial phases" not in caplog.text
    assert "unit-test-key" not in caplog.text and "private refusal diagnostic" not in caplog.text


def test_pipeline_request_ids_correlate_threads_and_stay_request_local(caplog, monkeypatch):
    from app.main import app, get_clinical_trials, get_planner, get_settings

    caplog.set_level(logging.DEBUG, logger=logger.name)

    async def run():
        settings = Settings(_env_file=None)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"studies": [raw_study(1)], "totalCount": 1}
                )
            )
        ) as upstream:
            planner = SimpleNamespace(
                plan=AsyncMock(return_value=QueryPlan.model_validate(plan_data()))
            )
            adapter = ClinicalTrialsClient(upstream, settings)
            monkeypatch.setattr(
                app,
                "dependency_overrides",
                {
                    get_settings: lambda: settings,
                    get_planner: lambda: planner,
                    get_clinical_trials: lambda: adapter,
                },
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                replies = await asyncio.gather(
                    *[
                        client.post(
                            "/api/v1/visualize", json={"query": "private-question-sentinel"}
                        )
                        for _ in range(2)
                    ]
                )
            assert all(reply.status_code == 200 for reply in replies)
            assert all(reply.json()["meta"]["retrieved_studies"] == 1 for reply in replies)
        assert request_id.get() == "-"

    asyncio.run(run())
    ids = {entry["request_id"] for entry in events(caplog, "pipeline.start")}
    assert len(ids) == 2 and "-" not in ids
    for event in ("ctgov.request", "pipeline.aggregate.complete", "pipeline.complete"):
        assert {entry["request_id"] for entry in events(caplog, event)} == ids
    assert "private-question-sentinel" not in caplog.text


@pytest.mark.parametrize(
    "decision,code",
    [("unsupported", "unsupported_query"), ("needs_clarification", "ambiguous_query")],
)
def test_planner_decision_logs_keep_unanswerable_classifications(caplog, decision, code):
    caplog.set_level(logging.DEBUG, logger=logger.name)
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(decision=decision))
    assert exc.value.code == code
    assert events(caplog, "planner.decision")[-1]["decision"] == decision
    assert events(caplog, "planner.failed")[-1]["category"] == code


def test_evaluator_is_opt_in_and_restores_logger(monkeypatch, capsys):
    from scripts import evaluate_planner

    def unexpected(*args, **kwargs):
        pytest.fail("A plain evaluation invocation must not start the network")

    monkeypatch.setattr(evaluate_planner.asyncio, "run", unexpected)
    assert evaluate_planner.cli([]) == 0
    assert "--live" in capsys.readouterr().out


def test_evaluator_detects_a_supported_plan_for_a_negative_case(monkeypatch, capsys):
    from scripts import evaluate_planner

    before = logger.level, logger.propagate, logger.disabled, list(logger.handlers)
    monkeypatch.setattr(evaluate_planner, "Settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        evaluate_planner,
        "OpenAIPlanner",
        lambda settings: SimpleNamespace(
            plan=AsyncMock(return_value=QueryPlan.model_validate(plan_data()))
        ),
    )
    case = {
        "id": "negative",
        "query": "private-question-sentinel",
        "plan": plan_data(),
        "expected": "ambiguous_query",
    }
    assert asyncio.run(evaluate_planner.evaluate([case])) == 1
    assert (logger.level, logger.propagate, logger.disabled, list(logger.handlers)) == before
    output = capsys.readouterr().out
    assert json.loads(output)["matches_expected"] is False
    assert "private-question-sentinel" not in output


@pytest.mark.parametrize("case_id", ["phase_reported", "network_reported"])
def test_broad_supported_plans_still_fail_closed_at_retrieval_caps(caplog, monkeypatch, case_id):
    from pathlib import Path

    from app.main import app, get_clinical_trials, get_planner, get_settings

    case = next(
        case
        for case in json.loads(
            (Path(__file__).parent / "fixtures/planner_reliability.json").read_text()
        )
        if case["id"] == case_id
    )
    caplog.set_level(logging.DEBUG, logger=logger.name)

    async def run():
        settings = Settings(_env_file=None, ctgov_max_studies=1)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"studies": [], "totalCount": 2})
            )
        ) as upstream:
            planner = SimpleNamespace(
                plan=AsyncMock(return_value=QueryPlan.model_validate(case["plan"]))
            )
            monkeypatch.setattr(
                app,
                "dependency_overrides",
                {
                    get_settings: lambda: settings,
                    get_planner: lambda: planner,
                    get_clinical_trials: lambda: ClinicalTrialsClient(upstream, settings),
                },
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as http:
                reply = await http.post("/api/v1/visualize", json={"query": case["query"]})
            assert reply.status_code == 422
            assert reply.json()["error"]["code"] == "incomplete_retrieval"

    asyncio.run(run())
    assert events(caplog, "pipeline.failed")[-1]["category"] == "incomplete_retrieval"
    assert not events(caplog, "pipeline.aggregate.complete")
