"""Offline checks for the opt-in verification tool; never contact either upstream."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.charts import build_chart
from app.clinical_trials import Intervention, NormalizedStudy, RetrievalResult
from app.errors import ServiceError
from app.models import QueryPlan, VisualizationRequest
from scripts import smoke_citations, smoke_examples


def scenario(name):
    first = NormalizedStudy(
        nct_id="NCT00000001",
        phases=("PHASE1",),
        start_year=2020,
        sponsor_name="Sponsor",
        countries=("Canada", "United States"),
        interventions=(Intervention(name="Drug", type="DRUG"),),
        enrollment_count=25,
        enrollment_type="ESTIMATED",
    )
    second = first.model_copy(
        update={
            "nct_id": "NCT00000002",
            "phases": ("PHASE2",),
            "start_year": 2021,
            "enrollment_count": 75,
            "countries": ("United States",),
            "interventions": (
                Intervention(name="Drug", type="DRUG"),
                Intervention(name="Biological", type="BIOLOGICAL"),
                Intervention(name="Unclassified", type=None),
            ),
        }
    )
    intent, grouping, chart = {
        "phase_distribution": ("distribution", "phase", "bar_chart"),
        "annual_trends": ("time_trend", "start_year", "time_series"),
        "comparison_overlap": ("comparison", "phase", "grouped_bar_chart"),
        "enrollment_recruiting": ("distribution", "enrollment_count", "histogram"),
        "network_recruiting": ("relationship", "sponsor_drug", "network_graph"),
        "geography": ("geographic", "country", "bar_chart"),
        "intervention_types": ("distribution", "intervention", "bar_chart"),
    }[name]
    comparison = (
        {"field": "drug_name", "values": ["Drug A", "Drug B"]} if intent == "comparison" else None
    )
    plan = QueryPlan.model_validate(
        {
            "intent": intent,
            "measure": "study_count",
            "filters": {},
            "group_by": grouping,
            "chart_type": chart,
            "comparison": comparison,
        }
    )
    cohorts = [[first, second], [second]] if comparison else [[first, second]]
    results = [
        RetrievalResult(
            studies=studies,
            total_matches=len(studies),
            retrieved_records=len(studies),
            pages_fetched=1,
        )
        for studies in cohorts
    ]
    return plan, build_chart(plan, results), results


async def exercise(tmp_path, name, *, change=None, status=200, save=False):
    plan, output, results = scenario(name)
    request = VisualizationRequest(query="Synthetic verification fixture")
    payload = output.model_dump(mode="json")
    if change:
        change(payload, results)
    adapter = SimpleNamespace(search=AsyncMock(side_effect=results))
    expected_request = request.model_dump(mode="json")

    def reply(sent):
        assert json.loads(sent.content) == expected_request
        return httpx.Response(status, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        success = await smoke_examples.check_case(
            name, "http://test", http, adapter, request, plan, tmp_path / "new" if save else None
        )
    return success, adapter


@pytest.mark.parametrize("name", [*smoke_examples.FEATURED, "geography", "intervention_types"])
def test_independent_live_checker_with_synthetic_groups(tmp_path, name):
    success, adapter = asyncio.run(exercise(tmp_path, name))
    assert success
    assert adapter.search.await_count == (2 if name == "comparison_overlap" else 1)
    assert not (tmp_path / "new").exists()


def test_explicit_output_directory_saves_verified_run(tmp_path):
    success, _ = asyncio.run(exercise(tmp_path, "phase_distribution", save=True))
    assert success
    evidence = json.loads(
        (tmp_path / "new/outputs/phase_distribution.verification.json").read_text()
    )
    assert evidence["independent_retrieval_verified"] is True
    assert evidence["retrieved_studies"] == 2
    assert (tmp_path / "new/requests/phase_distribution.json").is_file()


@pytest.mark.parametrize("failure", ["counts", "interpretation", "incomplete", "upstream"])
def test_verification_failures_save_nothing(tmp_path, failure):
    def change(payload, results):
        if failure == "counts":
            payload["visualization"]["data"][0]["trial_count"] = 0
        elif failure == "interpretation":
            payload["meta"]["filters"]["country"] = "Canada"
        elif failure == "incomplete":
            results[0] = results[0].model_copy(update={"complete_results": False})
        else:
            results[0] = ServiceError("upstream_unavailable", "Synthetic transport failure", 503)

    success, _ = asyncio.run(exercise(tmp_path, "phase_distribution", change=change, save=True))
    assert not success
    assert not (tmp_path / "new").exists()


def test_http_error_never_becomes_empty_success(tmp_path, capsys):
    success, adapter = asyncio.run(exercise(tmp_path, "phase_distribution", status=503, save=True))
    assert not success
    adapter.search.assert_not_awaited()
    assert not (tmp_path / "new").exists()
    assert "HTTP 503" in capsys.readouterr().out


def test_cli_without_live_flag_does_not_start_network(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("Network execution must be explicit")

    monkeypatch.setattr(smoke_examples.asyncio, "run", unexpected)
    assert smoke_examples.cli([]) == 0
    assert "--live" in capsys.readouterr().out


@pytest.mark.parametrize("script", [smoke_examples, smoke_citations])
def test_cli_rejects_saving_live_data_in_the_repository(script, capsys):
    with pytest.raises(SystemExit) as exc:
        script.cli(["--live", "--output-dir", str(script.ROOT / "verification-results")])
    assert exc.value.code == 2
    assert "outside the repository" in capsys.readouterr().err
