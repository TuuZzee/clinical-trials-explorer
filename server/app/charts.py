"""Pure chart construction and validated dispatch across chart families."""

from collections.abc import Sequence

from app.aggregation import (
    CATEGORIES,
    _annual_counts,
    _categorical_counts,
    _category_order,
    _phase_warnings,
    _phases_by_study,
)
from app.aggregation import MISSING_PHASE as MISSING_PHASE
from app.aggregation import PHASE_LABELS as PHASE_LABELS
from app.aggregation import aggregate_phases as aggregate_phases
from app.clinical_trials import RetrievalResult
from app.errors import ServiceError
from app.models import (
    AxisEncoding,
    ChartType,
    CohortMeta,
    Encoding,
    Grouping,
    Measure,
    QueryIntent,
    QueryPlan,
    Visualization,
    VisualizationMeta,
    VisualizationResponse,
)


def _require_phase_distribution(plan: QueryPlan) -> None:
    if not (
        plan.intent == QueryIntent.DISTRIBUTION
        and plan.measure == Measure.STUDY_COUNT
        and plan.group_by == Grouping.PHASE
        and plan.chart_type == ChartType.BAR
        and plan.comparison is None
    ):
        raise ServiceError(
            "not_implemented",
            "The phase builder accepts phase-distribution bar charts only.",
            501,
        )


def build_phase_chart(plan: QueryPlan, result: RetrievalResult) -> VisualizationResponse:
    _require_phase_distribution(plan)
    phases = _phases_by_study(result.studies)
    unique_studies = len(phases)
    validate_retrieval(result)
    warnings = [*result.warnings, *_phase_warnings(phases)]
    if plan.filters.drug_name:
        warnings.append(
            "Intervention filters use registry search matching, not verified exact drug identity."
        )
    if plan.filters.sponsor:
        warnings.append("Sponsor search includes sponsors and collaborators.")
    qualifiers = [
        f"{label}: {value}"
        for label, value in (
            ("condition", plan.filters.condition),
            ("intervention", plan.filters.drug_name),
        )
        if value
    ]
    title = "Study phase distribution"
    if qualifiers:
        title += " for " + "; ".join(qualifiers)
    return VisualizationResponse(
        visualization=Visualization(
            type=ChartType.BAR,
            title=title,
            encoding=Encoding(
                x=AxisEncoding(field="phase", type="ordinal", title="Clinical trial phase"),
                y=AxisEncoding(field="trial_count", type="quantitative", title="Unique studies"),
            ),
            data=[row.model_dump() for row in aggregate_phases(result.studies)],
        ),
        meta=VisualizationMeta(
            filters=plan.filters,
            counting_unit="unique NCT IDs per phase",
            grouping="phase",
            retrieved_studies=unique_studies,
            total_matches=result.total_matches,
            complete_results=result.complete_results,
            warnings=warnings,
        ),
    )


def require_supported_plan(plan: QueryPlan) -> None:
    if (
        plan.intent == QueryIntent.RELATIONSHIP
        and plan.measure == Measure.STUDY_COUNT
        and plan.group_by == Grouping.SPONSOR_DRUG
        and plan.chart_type == ChartType.NETWORK
        and plan.comparison is None
    ):
        return
    if (
        plan.intent == QueryIntent.DISTRIBUTION
        and plan.measure == Measure.STUDY_COUNT
        and plan.group_by == Grouping.ENROLLMENT
        and plan.chart_type == ChartType.HISTOGRAM
        and plan.comparison is None
    ):
        return
    allowed = {
        QueryIntent.TIME_TREND: {Grouping.START_YEAR},
        QueryIntent.DISTRIBUTION: set(CATEGORIES) - {Grouping.COUNTRY},
        QueryIntent.GEOGRAPHIC: {Grouping.COUNTRY},
        QueryIntent.COMPARISON: set(CATEGORIES),
    }
    chart_types = {
        QueryIntent.TIME_TREND: ChartType.TIME_SERIES,
        QueryIntent.DISTRIBUTION: ChartType.BAR,
        QueryIntent.GEOGRAPHIC: ChartType.BAR,
        QueryIntent.COMPARISON: ChartType.GROUPED_BAR,
    }
    if (
        plan.measure != Measure.STUDY_COUNT
        or plan.group_by not in allowed.get(plan.intent, set())
        or plan.chart_type != chart_types.get(plan.intent)
    ):
        raise ServiceError("not_implemented", "This plan has no implemented chart builder.", 501)


def validate_retrieval(result: RetrievalResult) -> None:
    distinct = len({study.nct_id for study in result.studies})
    if not result.complete_results or (
        result.total_matches is not None and result.total_matches != distinct
    ):
        raise ServiceError(
            "incomplete_retrieval", "Cannot chart an incomplete or inconsistent retrieval.", 502
        )


def _collect_counts(
    plan: QueryPlan, results: Sequence[RetrievalResult]
) -> tuple[list[dict[str, int]], list[str]]:
    counts, warnings = [], []
    for index, result in enumerate(results):
        grouped, notes = (
            _annual_counts(result.studies)
            if plan.group_by == Grouping.START_YEAR
            else _categorical_counts(result.studies, plan.group_by)
        )
        counts.append(grouped)
        prefix = f"{plan.comparison.values[index]}: " if plan.comparison else ""
        warnings.extend(prefix + note for note in [*result.warnings, *notes])
    if plan.filters.drug_name or (plan.comparison and plan.comparison.field.value == "drug_name"):
        warnings.append(
            "Intervention filters use registry search matching, not verified exact drug identity."
        )
    if plan.filters.sponsor:
        warnings.append("Sponsor search includes sponsors and collaborators.")
    return counts, warnings


def _aligned_rows(
    plan: QueryPlan, field: str, counts: list[dict[str, int]]
) -> list[dict[str, str | int]]:
    if plan.group_by == Grouping.START_YEAR:
        categories = sorted(counts[0])
    else:
        categories = sorted(
            set().union(*(set(count) for count in counts)),
            key=lambda label: _category_order(label, plan.group_by),
        )
    rows = []
    for category in categories:
        for index, grouped in enumerate(counts):
            row = {field: category, "trial_count": grouped.get(category, 0)}
            if plan.comparison:
                row["cohort"] = plan.comparison.values[index]
            rows.append(row)
    return rows


def _chart_metadata(
    plan: QueryPlan, results: Sequence[RetrievalResult], warnings: list[str]
) -> VisualizationMeta:
    ids = [{study.nct_id for study in result.studies} for result in results]
    meta = VisualizationMeta(
        filters=plan.filters,
        grouping=plan.group_by.value,
        counting_unit=(
            "unique NCT IDs per category per cohort"
            if plan.comparison
            else f"unique NCT IDs per {plan.group_by.value}"
        ),
        retrieved_studies=len(set().union(*ids)),
        total_matches=None if plan.comparison else results[0].total_matches,
        complete_results=True,
        warnings=warnings,
    )
    if plan.comparison:
        meta.comparison_field = plan.comparison.field.value
        meta.overlap_studies = len(ids[0] & ids[1])
        meta.cohorts = [
            CohortMeta(
                label=label,
                filters=filters,
                retrieved_studies=len(ids[index]),
                total_matches=results[index].total_matches,
                complete_results=True,
                warnings=results[index].warnings,
            )
            for index, (label, filters) in enumerate(
                zip(plan.comparison.values, plan.cohort_filters(), strict=True)
            )
        ]
        meta.warnings.append(
            "Cohorts are independent searches; overlapping NCT IDs contribute to "
            "each cohort. retrieved_studies counts the distinct union, not the sum. "
            "Zeros mean an absent category in a completely retrieved cohort."
        )
    return meta


def build_chart(plan: QueryPlan, results: Sequence[RetrievalResult]) -> VisualizationResponse:
    require_supported_plan(plan)
    expected = 2 if plan.comparison else 1
    if len(results) != expected:
        raise ValueError("One complete retrieval is required per planned cohort")
    for result in results:
        validate_retrieval(result)
    if plan.chart_type == ChartType.NETWORK:
        from app.network import build_network

        return build_network(plan, results[0])
    if plan.chart_type == ChartType.HISTOGRAM:
        from app.histogram import build_histogram

        return build_histogram(plan, results[0])
    if plan.group_by == Grouping.PHASE and plan.comparison is None:
        return build_phase_chart(plan, results[0])
    field, axis_title, _ = CATEGORIES.get(plan.group_by, ("start_year", "Study start year", ""))
    counts, warnings = _collect_counts(plan, results)
    rows = _aligned_rows(plan, field, counts)
    meta = _chart_metadata(plan, results, warnings)
    return VisualizationResponse(
        visualization=Visualization(
            type=plan.chart_type,
            title=(
                f"Study counts by {axis_title.lower()}"
                + (
                    f": {plan.comparison.values[0]} versus {plan.comparison.values[1]}"
                    if plan.comparison
                    else ""
                )
            ),
            encoding=Encoding(
                x=AxisEncoding(
                    field=field,
                    type=(
                        "temporal"
                        if plan.group_by == Grouping.START_YEAR
                        else "ordinal"
                        if plan.group_by == Grouping.PHASE
                        else "nominal"
                    ),
                    title=axis_title,
                ),
                y=AxisEncoding(field="trial_count", type="quantitative", title="Unique studies"),
                series=(
                    AxisEncoding(field="cohort", type="nominal", title="Cohort")
                    if plan.comparison
                    else None
                ),
            ),
            data=rows,
        ),
        meta=meta,
    )
