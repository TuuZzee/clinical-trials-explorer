from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.charts import build_chart
from app.clinical_trials import NormalizedStudy, RetrievalResult
from app.schemas import QueryPlan, VisualizationResponse


def specimen(comparison=False, temporal=False):
    plan = QueryPlan.model_validate(
        {
            "intent": "comparison" if comparison else "time_trend" if temporal else "distribution",
            "measure": "study_count",
            "filters": {},
            "group_by": "start_year" if temporal else "phase",
            "chart_type": "grouped_bar_chart"
            if comparison
            else "time_series"
            if temporal
            else "bar_chart",
            "comparison": {"field": "condition", "values": ["A", "B"]} if comparison else None,
        }
    )
    retrieved = RetrievalResult(
        studies=[
            NormalizedStudy(
                nct_id="NCT00000001", phases=("PHASE1",), start_date="2020", start_year=2020
            )
        ],
        retrieved_records=1,
        total_matches=1,
        pages_fetched=1,
    )
    return build_chart(plan, [retrieved, retrieved] if comparison else [retrieved]).model_dump(
        mode="json"
    )


@pytest.mark.parametrize("value", [True, False, "1", 1.5, -1, None])
def test_strict_nonnegative_counts(value):
    output = specimen()
    output["visualization"]["data"][0]["trial_count"] = value
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "wrong_field",
        "duplicate",
        "series",
        "quantitative",
        "category_number",
        "category_null",
    ],
)
def test_encoding_and_rows_must_agree(change):
    output = specimen()
    chart = output["visualization"]
    if change == "missing":
        del chart["data"][0]["phase"]
    elif change == "extra":
        chart["data"][0]["extra"] = 1
    elif change == "wrong_field":
        chart["encoding"]["x"]["field"] = "unavailable"
    elif change == "duplicate":
        chart["data"].append(deepcopy(chart["data"][0]))
    elif change == "series":
        chart["encoding"]["series"] = {"field": "cohort", "title": "Cohort", "type": "nominal"}
    elif change == "quantitative":
        chart["encoding"]["y"]["type"] = "nominal"
    else:
        chart["data"][0]["phase"] = None if change == "category_null" else 123
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


@pytest.mark.parametrize("value", ["2020", "2020-02-01", "2020-99-01", "20-01-01", 2020])
def test_temporal_representation(value):
    output = specimen(temporal=True)
    output["visualization"]["data"][0]["start_year"] = value
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


@pytest.mark.parametrize(
    "change",
    ["overlap", "union", "total", "filters", "label", "incomplete", "unaligned", "too_large"],
)
def test_comparison_metadata_is_consistent(change):
    output = specimen(comparison=True)
    meta = output["meta"]
    if change == "overlap":
        meta["overlap_studies"] = 2
    elif change == "union":
        meta["retrieved_studies"] = 2
    elif change == "total":
        meta["total_matches"] = 2
    elif change == "filters":
        meta["cohorts"][0]["filters"]["condition"] = "different"
    elif change == "label":
        output["visualization"]["data"][0]["cohort"] = "unknown"
    elif change == "incomplete":
        meta["cohorts"][1]["complete_results"] = False
    elif change == "unaligned":
        output["visualization"]["data"].pop()
    elif change == "too_large":
        output["visualization"]["data"][0]["trial_count"] = 2
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


def test_zero_count_is_valid_only_when_it_is_an_actual_numeric_count():
    output = specimen(comparison=True)
    output["visualization"]["data"][0]["trial_count"] = 0
    assert VisualizationResponse.model_validate(output).visualization.data[0]["trial_count"] == 0


@pytest.mark.parametrize("data", [None, 123, "invalid"])
def test_malformed_data_is_a_validation_error(data):
    output = specimen()
    output["visualization"]["data"] = data
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


@pytest.mark.parametrize(
    "field,value",
    [("counting_unit", "patients"), ("grouping", "country"), ("retrieved_studies", True)],
)
def test_single_metadata_grouping_units_and_counts(field, value):
    output = specimen()
    output["meta"][field] = value
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


def test_annual_rows_must_be_chronological():
    output = specimen(temporal=True)
    output["visualization"]["data"].append({"start_year": "2019-01-01", "trial_count": 1})
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


def test_annual_counts_cannot_count_a_study_in_multiple_years():
    output = specimen(temporal=True)
    output["visualization"]["data"].append({"start_year": "2021-01-01", "trial_count": 1})
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


def test_shared_filters_cannot_restrict_the_compared_axis():
    output = specimen(comparison=True)
    output["meta"]["filters"]["condition"] = "A"
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)
