"""Reported enrollment aggregation and histogram construction."""

from collections.abc import Sequence

from app.aggregation import enrollment_bin, unique_studies
from app.clinical_trials import NormalizedStudy, RetrievalResult
from app.models import (
    ENROLLMENT_BINS,
    AxisEncoding,
    EnrollmentBin,
    EnrollmentSummary,
    HistogramEncoding,
    HistogramVisualization,
    QueryPlan,
    VisualizationMeta,
    VisualizationResponse,
)


def enrollment_histogram(
    studies: Sequence[NormalizedStudy],
) -> tuple[list[EnrollmentBin], EnrollmentSummary]:
    counts = [0] * len(ENROLLMENT_BINS)
    actual = estimated = unknown = excluded = 0
    for study in unique_studies(studies):
        value = study.enrollment_count
        if value is None:
            excluded += 1
            continue
        index = enrollment_bin(value)
        if index is not None:
            counts[index] += 1
        if study.enrollment_type == "ACTUAL":
            actual += 1
        elif study.enrollment_type == "ESTIMATED":
            estimated += 1
        else:
            unknown += 1
    included = sum(counts)
    summary = EnrollmentSummary(
        included_studies=included,
        excluded_studies=excluded,
        actual_studies=actual,
        estimated_studies=estimated,
        unknown_type_studies=unknown,
    )
    rows = (
        [
            EnrollmentBin(bin_lower=lower, bin_upper=upper, bin_label=label, trial_count=counts[i])
            for i, (lower, upper, label) in enumerate(ENROLLMENT_BINS)
        ]
        if included
        else []
    )
    return rows, summary


def build_histogram(plan: QueryPlan, result: RetrievalResult) -> VisualizationResponse:
    rows, summary = enrollment_histogram(result.studies)
    warnings = [
        *result.warnings,
        "Enrollment is the reported participant count, not a patient-outcome measure. "
        "Reported enrollment may be ACTUAL or ESTIMATED; estimates are not observed enrollment. "
        "Types are combined in bins and counted separately in metadata. "
        "Bins include their lower bound and exclude their upper bound; null means unbounded.",
    ]
    if summary.excluded_studies:
        warnings.append(
            f"{summary.excluded_studies} studies excluded due to "
            "missing or invalid enrollment counts."
        )
    if summary.unknown_type_studies:
        warnings.append(
            f"{summary.unknown_type_studies} included studies have "
            "missing or unrecognized enrollment type."
        )
    if plan.filters.sponsor:
        warnings.append("Sponsor search includes sponsors and collaborators.")
    if plan.filters.drug_name:
        warnings.append(
            "Intervention filters use registry search matching, not verified exact drug identity."
        )
    return VisualizationResponse(
        visualization=HistogramVisualization(
            title="Distribution of reported trial enrollment sizes",
            encoding=HistogramEncoding(
                x=AxisEncoding(
                    field="bin_lower", type="quantitative", title="Enrollment (participants)"
                ),
                x2=AxisEncoding(
                    field="bin_upper",
                    type="quantitative",
                    title="Upper enrollment bound (exclusive)",
                ),
                y=AxisEncoding(field="trial_count", type="quantitative", title="Unique studies"),
            ),
            data=rows,
        ),
        meta=VisualizationMeta(
            filters=plan.filters,
            grouping="enrollment_count",
            counting_unit="unique NCT IDs per enrollment bin",
            retrieved_studies=len(unique_studies(result.studies)),
            total_matches=result.total_matches,
            complete_results=result.complete_results,
            warnings=warnings,
            histogram=summary,
        ),
    )
