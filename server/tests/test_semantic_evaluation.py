"""Adversarial supported-looking model outputs: expected plans are not live predictions."""

import json
from pathlib import Path

import pytest

from app.errors import ServiceError
from app.schemas import VisualizationRequest
from scripts.smoke_examples import FEATURED, example_case
from tests.helpers.planner import output_response, run_planner

CASES = json.loads((Path(__file__).parent / "fixtures/semantic_evaluation.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_semantic_evaluation_matrix(case):
    request = VisualizationRequest(query=case["query"])
    if case["expected"] == "supported":
        plan, _ = run_planner(output_response(case["plan"]), request)
        assert plan.filters.model_dump(exclude_none=True, mode="json") == case["plan"]["filters"]
    else:
        with pytest.raises(ServiceError) as exc:
            run_planner(output_response(case["plan"]), request)
        assert exc.value.code == case["expected"]
        assert exc.value.status_code == 422


def test_explicit_filters_can_override_mentioned_constraints():
    case = CASES[0]
    request = VisualizationRequest(
        query=case["query"],
        condition="diabetes",
        drug_name="ibuprofen",
        country="Canada",
        sponsor="NIH",
        trial_status="COMPLETED",
        start_year=2018,
        end_year=2025,
    )
    plan, _ = run_planner(output_response(case["plan"]), request)
    assert plan.filters.condition == "diabetes"
    assert plan.filters.start_year == 2018 and plan.filters.end_year == 2025
    assert plan.filters.trial_status.value == "COMPLETED"


@pytest.mark.parametrize(
    "field,wrong",
    [("drug_name", "asthma"), ("condition", "aspirin"), ("country", "Acme"), ("sponsor", "France")],
)
def test_role_values_cannot_be_swapped_even_if_both_words_appear(field, wrong):
    case = CASES[0]
    data = {**case["plan"], "filters": {**case["plan"]["filters"], field: wrong}}
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(data), VisualizationRequest(query=case["query"]))
    assert exc.value.code == "ambiguous_query"


@pytest.mark.parametrize("name", FEATURED)
def test_openapi_questions_match_expected_plans(name):
    request, expected = example_case(name)
    actual, _ = run_planner(output_response(expected.model_dump(mode="json")), request)
    assert actual == expected


def test_recruiting_enrollment_histogram_is_representable_without_final_enrollment_assumptions():
    data = {
        "intent": "distribution",
        "measure": "study_count",
        "filters": {
            "condition": "breast cancer",
            "drug_name": "pembrolizumab",
            "trial_status": "RECRUITING",
        },
        "group_by": "enrollment_count",
        "chart_type": "histogram",
        "comparison": None,
    }
    request = VisualizationRequest(
        query="Show a histogram of enrollment sizes for recruiting breast "
        "cancer trials involving pembrolizumab"
    )
    plan, _ = run_planner(output_response(data), request)
    assert plan.chart_type.value == "histogram" and plan.filters.trial_status.value == "RECRUITING"


def test_recruiting_graph_must_not_infer_drug_eligibility_before_retrieval():
    data = {
        "intent": "relationship",
        "measure": "study_count",
        "filters": {
            "condition": "breast cancer",
            "drug_name": "pembrolizumab",
            "trial_status": "RECRUITING",
        },
        "group_by": "sponsor_drug",
        "chart_type": "network_graph",
        "comparison": None,
    }
    request = VisualizationRequest(
        query="Show a network of lead sponsors and drugs for recruiting "
        "breast cancer trials involving pembrolizumab"
    )
    plan, _ = run_planner(output_response(data), request)
    assert (
        plan.chart_type.value == "network_graph" and plan.filters.trial_status.value == "RECRUITING"
    )
