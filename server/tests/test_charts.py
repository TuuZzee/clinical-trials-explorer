import pytest
from pydantic import ValidationError

from app.charts import aggregate_phases, build_chart, build_phase_chart
from app.clinical_trials import Intervention, NormalizedStudy, RetrievalResult, normalize_study
from app.errors import ServiceError
from app.schemas import PhaseCount, QueryPlan, VisualizationResponse


def phase_plan(**overrides):
    return QueryPlan.model_validate(
        {
            "intent": "distribution",
            "measure": "study_count",
            "filters": {},
            "group_by": "phase",
            "chart_type": "bar_chart",
            "comparison": None,
            **overrides,
        }
    )


def study(nct_id, *phases):
    return NormalizedStudy(nct_id=nct_id, phases=phases)


def result(studies, **overrides):
    return RetrievalResult(
        studies=studies,
        total_matches=len({s.nct_id for s in studies}),
        retrieved_records=len(studies),
        pages_fetched=1,
        **overrides,
    )


def rows(studies):
    return [row.model_dump() for row in aggregate_phases(studies)]


def test_basic_distribution():
    assert rows(
        [
            study("NCT00000001", "PHASE2"),
            study("NCT00000002", "PHASE2"),
            study("NCT00000003", "PHASE3"),
        ]
    ) == [{"phase": "Phase 2", "trial_count": 2}, {"phase": "Phase 3", "trial_count": 1}]


def test_multi_phase_and_repeated_records_are_distinct_per_bucket():
    studies = [
        study("NCT00000001", "PHASE1", "PHASE2", "PHASE1"),
        study("NCT00000001", "PHASE2"),
        study("NCT00000002", "PHASE2"),
    ]
    assert rows(studies) == [
        {"phase": "Phase 1", "trial_count": 1},
        {"phase": "Phase 2", "trial_count": 2},
    ]
    response = build_phase_chart(phase_plan(), result(studies))
    assert response.meta.retrieved_studies == 2
    assert sum(row["trial_count"] for row in response.visualization.data) == 3
    assert any("sum of phase counts" in w for w in response.meta.warnings)


def test_missing_and_not_applicable_are_distinct():
    studies = [study("NCT00000001"), study("NCT00000002", "NA")]
    assert rows(studies) == [
        {"phase": "Not applicable", "trial_count": 1},
        {"phase": "Missing phase", "trial_count": 1},
    ]
    assert any(
        "no reported phase" in warning
        for warning in build_phase_chart(phase_plan(), result(studies)).meta.warnings
    )


def test_duplicate_with_reported_phase_does_not_gain_missing_bucket():
    assert rows([study("NCT00000001"), study("NCT00000001", "PHASE1")]) == [
        {"phase": "Phase 1", "trial_count": 1}
    ]


def test_stable_order_and_unknown_phases_preserved():
    studies = [
        study(
            "NCT00000001",
            "FUTURE_Z",
            "PHASE4",
            "NA",
            "PHASE2",
            "PHASE1",
            "EARLY_PHASE1",
            "PHASE3",
            "FUTURE_A",
        ),
        study("NCT00000002"),
    ]
    expected = [
        "Early Phase 1",
        "Phase 1",
        "Phase 2",
        "Phase 3",
        "Phase 4",
        "Not applicable",
        "Unrecognized phase: FUTURE_A",
        "Unrecognized phase: FUTURE_Z",
        "Missing phase",
    ]
    assert [row["phase"] for row in rows(studies)] == expected
    assert rows(list(reversed(studies))) == rows(studies)
    assert any(
        "Unrecognized" in w for w in build_phase_chart(phase_plan(), result(studies)).meta.warnings
    )


def test_empty_chart_has_no_synthetic_rows():
    response = build_phase_chart(phase_plan(), result([]))
    assert response.visualization.data == []
    assert response.meta.retrieved_studies == response.meta.total_matches == 0
    assert response.meta.complete_results


def test_response_contract_encodings_counts_and_filters():
    plan = phase_plan(
        filters={
            "condition": "breast cancer",
            "drug_name": "pembrolizumab",
            "sponsor": "National Cancer Institute",
        }
    )
    retrieved = result([study("NCT00000001", "PHASE2")], warnings=["Existing retrieval warning"])
    response = build_phase_chart(plan, retrieved)
    output = response.model_dump(mode="json")
    chart = output["visualization"]
    assert chart["type"] == "bar_chart"
    assert "breast cancer" in chart["title"] and "pembrolizumab" in chart["title"]
    assert chart["encoding"]["x"] == {
        "field": "phase",
        "type": "ordinal",
        "title": "Clinical trial phase",
    }
    assert chart["encoding"]["y"] == {
        "field": "trial_count",
        "type": "quantitative",
        "title": "Unique studies",
    }
    assert chart["encoding"]["series"] is None
    for row in chart["data"]:
        assert isinstance(row["phase"], str) and type(row["trial_count"]) is int
        assert chart["encoding"]["x"]["field"] in row
        assert chart["encoding"]["y"]["field"] in row
    assert response.meta.filters == plan.filters
    assert response.meta.source == "ClinicalTrials.gov Data API v2"
    assert response.meta.grouping == "phase"
    assert response.meta.counting_unit == "unique NCT IDs per phase"
    assert response.meta.total_matches == response.meta.retrieved_studies == 1
    assert response.meta.warnings[0] == "Existing retrieval warning"
    assert retrieved.warnings == ["Existing retrieval warning"]  # No mutation.
    assert VisualizationResponse.model_validate_json(response.model_dump_json()) == response


@pytest.mark.parametrize("count", [True, "1", 1.0, -1, 0])
def test_phase_count_is_strict_positive_integer(count):
    with pytest.raises(ValidationError):
        PhaseCount(phase="Phase 1", trial_count=count)


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
            "comparison": {"field": "drug_name", "values": ["aspirin", "ibuprofen"]},
        },
    ],
)
def test_phase_builder_rejects_other_valid_plans(overrides):
    with pytest.raises(ServiceError) as exc:
        build_phase_chart(phase_plan(**overrides), result([]))
    assert exc.value.code == "not_implemented" and exc.value.status_code == 501


def test_incomplete_retrieval_is_not_charted():
    with pytest.raises(ServiceError) as exc:
        build_phase_chart(phase_plan(), result([], complete_results=False))
    assert exc.value.code == "incomplete_retrieval"


def test_inconsistent_retrieval_total_is_not_charted():
    retrieved = result([])
    retrieved.total_matches = 1
    with pytest.raises(ServiceError) as exc:
        build_phase_chart(phase_plan(), retrieved)
    assert exc.value.code == "incomplete_retrieval"


def test_absent_total_remains_null():
    retrieved = result([study("NCT00000001", "PHASE1")])
    retrieved.total_matches = None
    response = build_phase_chart(phase_plan(), retrieved)
    assert response.meta.complete_results and response.meta.total_matches is None


def test_annual_order_precision_missing_dates_and_deduplication():
    studies = []
    for index, date_value in enumerate(["2022-03-04", "2020", "2020-02", None, "2021-99"]):
        studies.append(
            normalize_study(
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": f"NCT{index:08d}"},
                        "statusModule": {"startDateStruct": {"date": date_value}},
                    }
                }
            )
        )
    studies.append(studies[0])
    plan = phase_plan(intent="time_trend", group_by="start_year", chart_type="time_series")
    response = build_chart(plan, [result(studies)])
    assert response.visualization.data == [
        {"start_year": "2020-01-01", "trial_count": 2},
        {"start_year": "2022-01-01", "trial_count": 1},
    ]
    assert response.visualization.encoding.x.type == "temporal"
    assert response.meta.retrieved_studies == 5
    assert any("2 studies excluded" in w for w in response.meta.warnings)
    assert any("not posting or completion" in w for w in response.meta.warnings)


@pytest.mark.parametrize(
    "grouping,field,attribute,values,missing",
    [
        (
            "overall_status",
            "overall_status",
            "overall_status",
            ["RECRUITING", "UNKNOWN", None],
            "Missing status",
        ),
        (
            "sponsor_class",
            "sponsor_class",
            "sponsor_class",
            ["NIH", "INDUSTRY", None],
            "Missing lead sponsor class",
        ),
    ],
)
def test_scalar_distributions(grouping, field, attribute, values, missing):
    studies = [
        NormalizedStudy(nct_id=f"NCT{index:08d}", **{attribute: value})
        for index, value in enumerate(values)
    ]
    response = build_chart(phase_plan(group_by=grouping), [result(studies + studies[:1])])
    data = response.visualization.data
    assert {row[field]: row["trial_count"] for row in data} == {
        value or missing: 1 for value in values
    }
    assert data[-1][field] == missing
    assert response.meta.retrieved_studies == 3
    assert rows([]) == []


def test_intervention_types_are_multivalued_and_not_intervention_names():
    studies = [
        NormalizedStudy(
            nct_id="NCT00000001",
            interventions=(
                Intervention(name="A", type="DRUG"),
                Intervention(name="B", type="DRUG"),
                Intervention(name="C", type="BIOLOGICAL"),
            ),
        ),
        NormalizedStudy(
            nct_id="NCT00000002",
            interventions=(
                Intervention(name="D", type=None),
                Intervention(name="E", type="FUTURE_TYPE"),
            ),
        ),
        NormalizedStudy(nct_id="NCT00000003"),
    ]
    response = build_chart(phase_plan(group_by="intervention"), [result(studies)])
    assert response.visualization.data == [
        {"intervention_type": "BIOLOGICAL", "trial_count": 1},
        {"intervention_type": "DRUG", "trial_count": 1},
        {"intervention_type": "FUTURE_TYPE", "trial_count": 1},
        {"intervention_type": "Missing intervention type", "trial_count": 2},
    ]
    assert any("not mutually exclusive" in w for w in response.meta.warnings)


def test_location_countries_distinct_per_study_and_missing():
    studies = [
        NormalizedStudy(nct_id="NCT00000001", countries=("US", "US", "Korea")),
        NormalizedStudy(nct_id="NCT00000002", countries=("US",)),
        NormalizedStudy(nct_id="NCT00000003"),
    ]
    plan = phase_plan(intent="geographic", group_by="country")
    response = build_chart(plan, [result(studies + studies[:1])])
    assert response.visualization.data == [
        {"country": "Korea", "trial_count": 1},
        {"country": "US", "trial_count": 2},
        {"country": "Missing country", "trial_count": 1},
    ]
    assert response.meta.retrieved_studies == 3
    assert any("not sponsor country" in w for w in response.meta.warnings)
    reversed_response = build_chart(plan, [result(list(reversed(studies)))])
    assert response.visualization.data == reversed_response.visualization.data


def comparison_plan(group_by="phase", field="drug_name"):
    return phase_plan(
        intent="comparison",
        chart_type="grouped_bar_chart",
        group_by=group_by,
        comparison={"field": field, "values": ["Drug A", "Drug B"]},
    )


def test_comparison_overlap_alignment_and_metadata():
    plan = comparison_plan()
    first = result([study("NCT00000001", "PHASE1", "PHASE2"), study("NCT00000002", "PHASE2")])
    second = result([study("NCT00000002", "PHASE2"), study("NCT00000003", "PHASE3")])
    response = build_chart(plan, [first, second])
    assert response.visualization.data == [
        {"phase": "Phase 1", "trial_count": 1, "cohort": "Drug A"},
        {"phase": "Phase 1", "trial_count": 0, "cohort": "Drug B"},
        {"phase": "Phase 2", "trial_count": 2, "cohort": "Drug A"},
        {"phase": "Phase 2", "trial_count": 1, "cohort": "Drug B"},
        {"phase": "Phase 3", "trial_count": 0, "cohort": "Drug A"},
        {"phase": "Phase 3", "trial_count": 1, "cohort": "Drug B"},
    ]
    assert response.meta.retrieved_studies == 3 and response.meta.overlap_studies == 1
    assert response.meta.total_matches is None
    assert response.meta.comparison_field == "drug_name"
    assert [c.retrieved_studies for c in response.meta.cohorts] == [2, 2]
    assert [c.filters.drug_name for c in response.meta.cohorts] == ["Drug A", "Drug B"]
    assert response.visualization.encoding.series.field == "cohort"
    assert any("distinct union" in w for w in response.meta.warnings)


@pytest.mark.parametrize(
    "group_by", ["phase", "overall_status", "sponsor_class", "intervention", "country"]
)
@pytest.mark.parametrize("empty_first", [True, False])
def test_empty_cohort_is_aligned_with_zeros(group_by, empty_first):
    first, second = result([]), result([study("NCT00000001", "PHASE2")])
    if not empty_first:
        first, second = second, first
    response = build_chart(comparison_plan(group_by, field="condition"), [first, second])
    counts = {row["cohort"]: row["trial_count"] for row in response.visualization.data}
    assert counts == {"Drug A": int(not empty_first), "Drug B": int(empty_first)}
    assert response.meta.comparison_field == "condition"
    assert response.meta.retrieved_studies == 1 and response.meta.overlap_studies == 0


def test_both_cohorts_empty_have_no_fabricated_categories():
    response = build_chart(comparison_plan(), [result([]), result([])])
    assert response.visualization.data == []
    assert response.meta.retrieved_studies == response.meta.overlap_studies == 0


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("failure", ["incomplete", "mismatch"])
def test_each_cohort_must_be_complete(index, failure):
    retrieved = [result([]), result([])]
    if failure == "incomplete":
        retrieved[index].complete_results = False
    else:
        retrieved[index].total_matches = 1
    with pytest.raises(ServiceError) as exc:
        build_chart(comparison_plan(), retrieved)
    assert exc.value.code == "incomplete_retrieval"


@pytest.mark.parametrize(
    "grouping", ["start_year", "overall_status", "sponsor_class", "intervention", "country"]
)
def test_single_groupings_empty(grouping):
    plan = phase_plan(
        group_by=grouping,
        intent="time_trend"
        if grouping == "start_year"
        else "geographic"
        if grouping == "country"
        else "distribution",
        chart_type="time_series" if grouping == "start_year" else "bar_chart",
    )
    response = build_chart(plan, [result([])])
    assert response.visualization.data == [] and response.meta.retrieved_studies == 0
