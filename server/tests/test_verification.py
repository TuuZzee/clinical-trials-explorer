"""Independent verification of scalar groupings against synthetic studies."""

import pytest

from app.charts import build_chart
from app.clinical_trials import NormalizedStudy, RetrievalResult
from app.models import QueryPlan, VisualizationResponse
from scripts.verification import reference_counts, verify_results


@pytest.mark.parametrize(
    "grouping,attribute,values,expected",
    [
        (
            "overall_status",
            "overall_status",
            ["RECRUITING", "UNKNOWN", "FUTURE_STATUS", None],
            {"RECRUITING": 1, "UNKNOWN": 1, "FUTURE_STATUS": 1, "Missing status": 1},
        ),
        (
            "sponsor_class",
            "sponsor_class",
            ["NIH", "INDUSTRY", "FUTURE_CLASS", None],
            {"NIH": 1, "INDUSTRY": 1, "FUTURE_CLASS": 1, "Missing lead sponsor class": 1},
        ),
    ],
)
@pytest.mark.parametrize("comparison", [False, True])
def test_scalar_reference_verifies_distinct_counts_and_rejects_wrong_values(
    grouping, attribute, values, expected, comparison
):
    studies = [
        NormalizedStudy(nct_id=f"NCT{index:08d}", **{attribute: value})
        for index, value in enumerate(values)
    ]
    # Duplicate records must not inflate independent reference counts.
    assert reference_counts([*studies, studies[0]], grouping) == expected
    plan = QueryPlan.model_validate(
        {
            "intent": "comparison" if comparison else "distribution",
            "measure": "study_count",
            "filters": {},
            "group_by": grouping,
            "chart_type": "grouped_bar_chart" if comparison else "bar_chart",
            "comparison": {"field": "condition", "values": ["Condition A", "Condition B"]}
            if comparison
            else None,
        }
    )
    cohorts = [[*studies, studies[0]], [studies[0], studies[-1]]] if comparison else [studies]
    results = [
        RetrievalResult(
            studies=cohort,
            total_matches=len({study.nct_id for study in cohort}),
            retrieved_records=len(cohort),
            pages_fetched=1,
        )
        for cohort in cohorts
    ]
    output = build_chart(plan, results)
    evidence = verify_results(output, results)
    assert evidence["cohort_sizes"] == ([4, 2] if comparison else [4])
    assert evidence["overlap_studies"] == (2 if comparison else None)
    assert evidence["zero_rows"] == (2 if comparison else 0)

    altered = output.model_dump(mode="json")
    altered["visualization"]["data"][0]["trial_count"] = 0
    # This remains structurally valid; the independent source counts must reject it.
    incorrect = VisualizationResponse.model_validate(altered)
    with pytest.raises(ValueError, match="Independent category counts"):
        verify_results(incorrect, results)
