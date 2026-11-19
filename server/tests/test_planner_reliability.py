"""Injected plans test server validation, not the accuracy of a live model."""

import json
from pathlib import Path

import pytest

from app.errors import ServiceError
from app.models import QueryPlan, VisualizationRequest
from tests.helpers.planner import output_response, run_planner

CASES = json.loads((Path(__file__).parent / "fixtures/planner_reliability.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reported_questions_and_comparison_constraint_guards(case):
    request = VisualizationRequest(query=case["query"])
    if case["expected"] == "supported":
        plan, _ = run_planner(output_response(case["plan"]), request)
        assert plan == QueryPlan.model_validate(case["plan"])
    else:
        with pytest.raises(ServiceError) as exc:
            run_planner(output_response(case["plan"]), request)
        assert exc.value.code == case["expected"]


@pytest.mark.parametrize(
    "members",
    [["nivolumab"], ["nivolumab", "nivolumab"], ["nivolumab", "ipilimumab", "pembrolizumab"]],
)
def test_comparison_connectors_do_not_relax_member_cardinality(members):
    case = next(case for case in CASES if case["id"] == "comparison_vs_dot")
    plan = {**case["plan"], "comparison": {"field": "drug_name", "values": members}}
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan), VisualizationRequest(query=case["query"]))
    assert exc.value.code == "planner_invalid_response"


def test_connector_elsewhere_does_not_hide_an_unrepresented_descriptor():
    case = next(case for case in CASES if case["id"] == "comparison_versus")
    with pytest.raises(ServiceError, match="descriptor"):
        run_planner(
            output_response(case["plan"]),
            VisualizationRequest(
                query="Compare phases of nivolumab and ipilimumab resistant versus sensitive trials"
            ),
        )
