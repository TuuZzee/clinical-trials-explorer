import pytest
from pydantic import ValidationError

from app.charts import build_chart
from app.clinical_trials import NormalizedStudy, normalize_study
from app.errors import ServiceError
from app.extra_charts import enrollment_histogram
from app.schemas import ENROLLMENT_BINS, QueryPlan, VisualizationResponse
from tests.helpers.charts import retrieval


def histogram_plan(**overrides):
    return QueryPlan.model_validate(
        {
            "intent": "distribution",
            "measure": "study_count",
            "filters": {},
            "group_by": "enrollment_count",
            "chart_type": "histogram",
            "comparison": None,
            **overrides,
        }
    )


def enrolled(index, count, kind="ACTUAL"):
    return NormalizedStudy(nct_id=f"NCT{index:08d}", enrollment_count=count, enrollment_type=kind)


@pytest.mark.parametrize(
    "value,index",
    [
        (0, 0),
        (9, 0),
        (10, 1),
        (49, 1),
        (50, 2),
        (99, 2),
        (100, 3),
        (499, 3),
        (500, 4),
        (999, 4),
        (1000, 5),
        (4999, 5),
        (5000, 6),
        (1000000, 6),
    ],
)
def test_enrollment_boundaries(value, index):
    rows, summary = enrollment_histogram([enrolled(1, value)])
    assert [row.trial_count for row in rows] == [
        int(i == index) for i in range(len(ENROLLMENT_BINS))
    ]
    assert summary.included_studies == 1 and summary.excluded_studies == 0


@pytest.mark.parametrize("raw", [None, -1, True, False, "30", 30.0, {}, [], "bad"])
def test_invalid_enrollment_is_excluded_by_normalizer(raw):
    record = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001"},
            "designModule": {"enrollmentInfo": {"count": raw, "type": "ESTIMATED"}},
        }
    }
    study = normalize_study(record)
    assert study.enrollment_count is None
    rows, summary = enrollment_histogram([study])
    assert rows == [] and summary.excluded_studies == 1


def test_enrollment_types_exclusions_zero_and_duplicate_policy():
    studies = [
        enrolled(1, 0),
        enrolled(2, 50, "ESTIMATED"),
        enrolled(3, 20, None),
        enrolled(4, 1000, "OTHER_TYPE"),
        enrolled(5, None),
        enrolled(1, 9999),
    ]
    response = build_chart(histogram_plan(), [retrieval(studies)])
    summary = response.meta.histogram
    assert summary.model_dump() == {
        "included_studies": 4,
        "excluded_studies": 1,
        "actual_studies": 1,
        "estimated_studies": 1,
        "unknown_type_studies": 2,
    }
    assert sum(row.trial_count for row in response.visualization.data) == 4
    assert response.visualization.data[0].trial_count == 1
    assert response.visualization.data[-1].trial_count == 0
    assert response.meta.retrieved_studies == 5
    assert any("estimates are not observed" in w for w in response.meta.warnings)
    assert any("1 studies excluded" in w for w in response.meta.warnings)
    assert response.visualization.encoding.x.field == "bin_lower"
    assert response.visualization.encoding.x2.field == "bin_upper"
    assert response.visualization.encoding.y.field == "trial_count"
    assert VisualizationResponse.model_validate_json(response.model_dump_json()) == response


@pytest.mark.parametrize("studies", [[], [enrolled(1, None)]])
def test_empty_or_all_invalid_histograms_have_no_synthetic_bins(studies):
    response = build_chart(histogram_plan(), [retrieval(studies)])
    assert response.visualization.data == []
    assert response.meta.histogram.excluded_studies == len(studies)


def test_histogram_order_stable_without_duplicate_conflicts():
    studies = [enrolled(1, 20), enrolled(2, 5000), enrolled(3, 0)]
    assert enrollment_histogram(studies) == enrollment_histogram(list(reversed(studies)))


@pytest.mark.parametrize(
    "mutate",
    [
        "bounds",
        "count",
        "bool",
        "encoding",
        "excluded",
        "type_totals",
        "grouping",
        "missing_meta",
        "cohorts",
    ],
)
def test_histogram_response_contract_rejects_inconsistent_data(mutate):
    response = build_chart(histogram_plan(), [retrieval([enrolled(1, 30)])]).model_dump(mode="json")
    if mutate == "bounds":
        response["visualization"]["data"][0]["bin_upper"] = 11
    elif mutate in {"count", "bool"}:
        response["visualization"]["data"][1]["trial_count"] = "1" if mutate == "count" else True
    elif mutate == "encoding":
        response["visualization"]["encoding"]["x"]["field"] = "not_a_bin"
    elif mutate == "excluded":
        response["meta"]["histogram"]["excluded_studies"] = 1
    elif mutate == "type_totals":
        response["meta"]["histogram"]["estimated_studies"] = 1
    elif mutate == "grouping":
        response["meta"]["grouping"] = "phase"
    elif mutate == "missing_meta":
        response["meta"]["histogram"] = None
    else:
        response["meta"]["comparison_field"] = "drug_name"
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(response)


def test_histogram_requires_complete_population():
    with pytest.raises(ServiceError) as exc:
        build_chart(histogram_plan(), [retrieval([], complete_results=False)])
    assert exc.value.code == "incomplete_retrieval"
