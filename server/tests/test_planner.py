import asyncio
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.clinical_trials import compile_filters
from app.config import Settings
from app.errors import ServiceError
from app.planner import OpenAIPlanner, merge_request
from app.schemas import QueryPlan, VisualizationRequest
from tests.helpers.planner import output_response, plan_data, run_planner


@pytest.mark.parametrize(
    ("question", "data"),
    [
        ("Show phases of breast cancer trials", plan_data(filters={"condition": "breast cancer"})),
        (
            "How many pembrolizumab trials started each year since 2015?",
            plan_data(
                intent="time_trend",
                filters={"drug_name": "pembrolizumab", "start_year": 2015},
                group_by="start_year",
                chart_type="time_series",
            ),
        ),
        (
            "Which countries have the most recruiting diabetes trials?",
            plan_data(
                intent="geographic",
                filters={"condition": "diabetes", "trial_status": "RECRUITING"},
                group_by="country",
            ),
        ),
        (
            "Count recruiting asthma trials by sponsor class",
            plan_data(
                filters={"condition": "asthma", "trial_status": "RECRUITING"},
                group_by="sponsor_class",
            ),
        ),
        (
            "Show asthma trials by status",
            plan_data(filters={"condition": "asthma"}, group_by="overall_status"),
        ),
        (
            "Show asthma trials by intervention type",
            plan_data(filters={"condition": "asthma"}, group_by="intervention"),
        ),
    ],
)
def test_supported_interpretations(question, data):
    result, calls = run_planner(output_response(data), VisualizationRequest(query=question))
    assert result == QueryPlan.model_validate(data)
    assert len(calls) == 1
    compiled = compile_filters(result.filters)
    assert compiled["countTotal"] == "true"


@pytest.mark.parametrize(
    ("field", "values", "query"),
    [
        (
            "drug_name",
            ["pembrolizumab", "nivolumab"],
            "Compare phases of pembrolizumab and nivolumab trials",
        ),
        ("condition", ["asthma", "diabetes"], "Compare trial phases for asthma versus diabetes"),
    ],
)
def test_independent_comparison_cohorts(field, values, query):
    data = plan_data(
        intent="comparison",
        chart_type="grouped_bar_chart",
        filters={"country": "United States"},
        comparison={"field": field, "values": values},
    )
    result, _ = run_planner(
        output_response(data), VisualizationRequest(query=query, country="United States")
    )
    cohorts = result.cohort_filters()
    assert [getattr(f, field) for f in cohorts] == values
    assert all(f.country == "United States" for f in cohorts)
    assert getattr(result.filters, field) is None
    assert compile_filters(cohorts[0]) != compile_filters(cohorts[1])


def test_explicit_non_null_filters_win_and_null_does_not_clear():
    data = plan_data(
        filters={
            "condition": "asthma",
            "drug_name": "aspirin",
            "country": "Canada",
            "sponsor": "NIH",
            "trial_status": "RECRUITING",
            "start_year": 2010,
            "end_year": 2020,
        }
    )
    request = VisualizationRequest(
        query="Show asthma trial phases",
        condition="diabetes",
        drug_name="ibuprofen",
        country="United States",
        sponsor="NCI",
        trial_status="COMPLETED",
        start_year=2015,
        end_year=2025,
        preferred_chart="bar_chart",
    )
    result, _ = run_planner(output_response(data), request)
    assert result.filters.model_dump(mode="json") == {
        "condition": "diabetes",
        "drug_name": "ibuprofen",
        "country": "United States",
        "sponsor": "NCI",
        "trial_status": "COMPLETED",
        "start_year": 2015,
        "end_year": 2025,
    }
    result, _ = run_planner(
        output_response(plan_data(filters={"condition": "asthma"})),
        VisualizationRequest(query="Show asthma trial phases", condition=None),
    )
    assert result.filters.condition == "asthma"


def test_conflicting_merged_years():
    data = plan_data(filters={"end_year": 2020})
    with pytest.raises(ServiceError) as exc:
        run_planner(
            output_response(data),
            VisualizationRequest(query="Show trial phases through 2020", start_year=2025),
        )
    assert exc.value.code == "conflicting_filters" and exc.value.status_code == 422


@pytest.mark.parametrize("preferred", ["time_series", "grouped_bar_chart"])
def test_incompatible_explicit_chart(preferred):
    with pytest.raises(ServiceError) as exc:
        run_planner(
            output_response(plan_data()),
            VisualizationRequest(query="Show trial phases", preferred_chart=preferred),
        )
    assert exc.value.code == "unsupported_query"


def test_explicit_comparison_axis_conflict():
    data = plan_data(
        intent="comparison",
        chart_type="grouped_bar_chart",
        comparison={"field": "drug_name", "values": ["aspirin", "ibuprofen"]},
    )
    with pytest.raises(ServiceError) as exc:
        run_planner(
            output_response(data),
            VisualizationRequest(
                query="Compare phases for aspirin and ibuprofen", drug_name="aspirin"
            ),
        )
    assert exc.value.code == "conflicting_filters"


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent": "time_trend"},
        {"group_by": "country"},
        {"chart_type": "time_series"},
        {"intent": "comparison", "chart_type": "grouped_bar_chart"},
        {"comparison": {"field": "condition", "values": ["asthma", "diabetes"]}},
        {
            "intent": "comparison",
            "chart_type": "grouped_bar_chart",
            "group_by": "start_year",
            "comparison": {"field": "drug_name", "values": ["aspirin", "ibuprofen"]},
        },
    ],
)
def test_invalid_semantic_combinations(overrides):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan_data(**overrides)))
    assert exc.value.code == "unsupported_query" and exc.value.status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"measure": "survival"},
        {"group_by": "raw.field.path"},
        {"chart_type": "scatter_plot"},
        {"intent": "causal_inference"},
        {"filters": {"query.term": "raw"}},
        {"filters": {"condition": 'x" OR AREA[Phase]PHASE3'}},
        {"filters": {"start_year": "2020"}},
        {"filters": {"start_year": True}},
        {"filters": {"start_year": 2025, "end_year": 2020}},
        {"trial_count": 100},
        {"python": "print(100)"},
        {
            "intent": "comparison",
            "chart_type": "grouped_bar_chart",
            "comparison": {"field": "drug_name", "values": ["aspirin"]},
        },
        {
            "intent": "comparison",
            "chart_type": "grouped_bar_chart",
            "comparison": {"field": "drug_name", "values": ["aspirin", "ASPIRIN"]},
        },
    ],
)
def test_invalid_structured_output(overrides):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan_data(**overrides)))
    assert exc.value.code == "planner_invalid_response" and exc.value.status_code == 502


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        "null",
        "[]",
        '{"status":"supported","plan":null}',
        json.dumps({"status": "unsupported", "plan": plan_data()}),
    ],
)
def test_bad_envelope(text):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(text=text))
    assert exc.value.code == "planner_invalid_response"


@pytest.mark.parametrize(
    "decision,code",
    [("unsupported", "unsupported_query"), ("needs_clarification", "ambiguous_query")],
)
def test_unanswerable_decisions(decision, code):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(decision=decision), VisualizationRequest(query="Show trials"))
    assert exc.value.code == code and exc.value.status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        "Compare efficacy of aspirin and ibuprofen",
        "Show survival improvement for drug A",
        "Predict future trial counts",
        "Which drug works best for asthma?",
        "Forecast trials next year",
        "Show survival curves",
        "Compare response rates",
    ],
)
def test_unsupported_analysis_rejected_even_if_model_would_return_counts(query):
    async def run():
        # No SDK client or key is needed: deterministic guard runs first.
        return await OpenAIPlanner(Settings(_env_file=None, openai_api_key=None)).plan(
            VisualizationRequest(query=query)
        )

    with pytest.raises(ServiceError) as exc:
        asyncio.run(run())
    assert exc.value.code == "unsupported_query"


@pytest.mark.parametrize(
    "query",
    ["Show trial trends for the last 5 years", "Show phases this year", "Show trials recently"],
)
def test_relative_dates_need_clarification(query):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan_data()), VisualizationRequest(query=query))
    assert exc.value.code == "ambiguous_query"


@pytest.mark.parametrize(
    "filters",
    [
        {"condition": "diabetes"},
        {"drug_name": "aspirin"},
        {"country": "United States"},
        {"sponsor": "NIH"},
        {"start_year": 2015},
        {"end_year": 2025},
    ],
)
def test_invented_filters_are_rejected(filters):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan_data(filters=filters)))
    assert exc.value.code == "ambiguous_query"


def test_invented_comparison_member():
    data = plan_data(
        intent="comparison",
        chart_type="grouped_bar_chart",
        comparison={"field": "drug_name", "values": ["aspirin", "ibuprofen"]},
    )
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(data), VisualizationRequest(query="Compare phases for aspirin"))
    assert exc.value.code == "ambiguous_query"


def test_single_cohort_and_original_plan_not_mutated():
    original = QueryPlan.model_validate(plan_data(filters={"condition": "asthma"}))
    before = deepcopy(original)
    merged = merge_request(
        original, VisualizationRequest(query="Show asthma phases", condition="diabetes")
    )
    assert original == before
    assert merged.cohort_filters()[0].condition == "diabetes"


@pytest.mark.parametrize(
    "overrides",
    [
        {"openai_model": ""},
        {"openai_model": "  "},
        {"openai_timeout_seconds": 0},
        {"openai_timeout_seconds": 121},
        {"openai_max_output_tokens": 0},
        {"openai_max_output_tokens": 9000},
    ],
)
def test_planner_config_validation(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    "query",
    [
        "Show survival improvement for pembrolizumab",
        "Show average enrollment by phase",
        "Show Phase 3 asthma trials by country",
        "Show sponsor to device relationships",
        "Show trial counts by sponsor country",
    ],
)
def test_unsupported_question_classification(query):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(decision="unsupported"), VisualizationRequest(query=query))
    assert exc.value.code == "unsupported_query"


@pytest.mark.parametrize(
    "query",
    [
        "Show trials",
        "Compare phases of this drug and another one",
        "Show ongoing trials by country",
    ],
)
def test_ambiguous_question_classification(query):
    with pytest.raises(ServiceError) as exc:
        run_planner(
            output_response(decision="needs_clarification"), VisualizationRequest(query=query)
        )
    assert exc.value.code == "ambiguous_query"


@pytest.mark.parametrize(
    "values",
    [
        ["asthma", "diabetes", "cancer"],
        ["breast cancer", "breast  cancer"],
    ],
)
def test_invalid_cohort_cardinality_and_equivalent_phrases(values):
    with pytest.raises(ServiceError) as exc:
        run_planner(
            output_response(
                plan_data(
                    intent="comparison",
                    chart_type="grouped_bar_chart",
                    comparison={"field": "condition", "values": values},
                )
            )
        )
    assert exc.value.code == "planner_invalid_response"


def test_shared_comparison_axis_is_invalid():
    with pytest.raises(ServiceError) as exc:
        run_planner(
            output_response(
                plan_data(
                    intent="comparison",
                    chart_type="grouped_bar_chart",
                    filters={"condition": "asthma"},
                    comparison={"field": "condition", "values": ["asthma", "diabetes"]},
                )
            )
        )
    assert exc.value.code == "unsupported_query"


@pytest.mark.parametrize("query", ["Show trial phases", "Show not yet recruiting trial phases"])
def test_invented_or_contradictory_status(query):
    with pytest.raises(ServiceError) as exc:
        run_planner(
            output_response(plan_data(filters={"trial_status": "RECRUITING"})),
            VisualizationRequest(query=query),
        )
    assert exc.value.code == "ambiguous_query"


def test_enrollment_histogram_planning_and_filter_precedence():
    data = plan_data(
        group_by="enrollment_count", chart_type="histogram", filters={"condition": "asthma"}
    )
    plan, calls = run_planner(
        output_response(data),
        VisualizationRequest(
            query="Show a histogram of asthma trial enrollment sizes", condition="diabetes"
        ),
    )
    assert plan.chart_type.value == "histogram" and plan.group_by.value == "enrollment_count"
    assert plan.filters.condition == "diabetes"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"group_by": "phase", "chart_type": "histogram"},
        {"group_by": "enrollment_count", "chart_type": "bar_chart"},
        {
            "intent": "comparison",
            "group_by": "enrollment_count",
            "chart_type": "grouped_bar_chart",
            "comparison": {"field": "condition", "values": ["asthma", "diabetes"]},
        },
    ],
)
def test_unsupported_histogram_combinations(overrides):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan_data(**overrides)))
    assert exc.value.code == "unsupported_query"


def test_lead_sponsor_drug_network_plan_and_preference():
    data = plan_data(
        intent="relationship",
        group_by="sponsor_drug",
        chart_type="network_graph",
        filters={"condition": "asthma"},
    )
    plan, calls = run_planner(
        output_response(data),
        VisualizationRequest(
            query="Show a network of lead sponsors and drugs for asthma trials",
            condition="diabetes",
            preferred_chart="network_graph",
        ),
    )
    assert plan.intent.value == "relationship" and plan.group_by.value == "sponsor_drug"
    assert plan.filters.condition == "diabetes" and len(calls) == 1
    schema = json.loads(calls[0].content)["text"]["format"]["schema"]
    assert "sponsor_drug" in schema["$defs"]["Grouping"]["enum"]
    assert "network_graph" in schema["$defs"]["ChartType"]["enum"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent": "relationship"},
        {"chart_type": "network_graph"},
        {"intent": "relationship", "group_by": "intervention", "chart_type": "network_graph"},
        {
            "intent": "comparison",
            "group_by": "sponsor_drug",
            "chart_type": "network_graph",
            "comparison": {"field": "drug_name", "values": ["A", "B"]},
        },
    ],
)
def test_unsupported_relationship_combinations(overrides):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan_data(**overrides)))
    assert exc.value.code == "unsupported_query"


@pytest.mark.parametrize(
    "query",
    [
        "Show drug-condition networks",
        "Show sponsor device relationships",
        "Show commercial partnerships between sponsors and drugs",
    ],
)
def test_unsupported_relationship_questions(query):
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(decision="unsupported"), VisualizationRequest(query=query))
    assert exc.value.code == "unsupported_query"
