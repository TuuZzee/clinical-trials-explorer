"""Response metadata and validation across chart families."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .base import StrictModel
from .charts import Visualization
from .citations import CitationBundle
from .enums import ChartType
from .histogram import EnrollmentSummary, HistogramVisualization
from .network import GraphSummary, NetworkVisualization
from .requests import StudyFilters


class CohortMeta(StrictModel):
    label: str
    filters: StudyFilters
    retrieved_studies: Annotated[int, Field(strict=True, ge=0)]
    total_matches: Annotated[int, Field(strict=True, ge=0)] | None
    complete_results: bool
    warnings: list[str] = Field(default_factory=list)


class VisualizationMeta(StrictModel):
    source: Literal["ClinicalTrials.gov Data API v2"] = "ClinicalTrials.gov Data API v2"
    filters: StudyFilters
    counting_unit: str
    grouping: str
    retrieved_studies: int = Field(strict=True, ge=0)
    total_matches: int | None = Field(default=None, strict=True, ge=0)
    complete_results: bool
    warnings: list[str] = Field(default_factory=list)
    comparison_field: Literal["drug_name", "condition"] | None = None
    cohorts: list[CohortMeta] = Field(default_factory=list)
    overlap_studies: Annotated[int, Field(strict=True, ge=0)] | None = None
    histogram: EnrollmentSummary | None = None
    graph: GraphSummary | None = None


class VisualizationResponse(StrictModel):
    visualization: Annotated[
        Visualization | HistogramVisualization | NetworkVisualization, Field(discriminator="type")
    ]
    meta: VisualizationMeta

    @model_validator(mode="after")
    def consistent_metadata(self) -> Self:
        chart, meta = self.visualization, self.meta
        if isinstance(chart, NetworkVisualization):
            _validate_network_metadata(chart, meta)
            return self
        if meta.graph is not None:
            raise ValueError("Only network graphs may have graph metadata")
        if isinstance(chart, HistogramVisualization):
            _validate_histogram_metadata(chart, meta)
            return self
        if meta.histogram is not None:
            raise ValueError("Only histograms may have enrollment-bin metadata")
        _validate_cartesian_metadata(chart, meta)
        return self


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorResponse(StrictModel):
    error: ErrorDetail


def _validate_network_metadata(chart: NetworkVisualization, meta: VisualizationMeta) -> None:
    summary = meta.graph
    if (
        summary is None
        or meta.histogram is not None
        or meta.grouping != "sponsor_drug"
        or meta.counting_unit != "unique NCT IDs per lead sponsor-drug edge"
        or not meta.complete_results
        or meta.cohorts
        or meta.comparison_field is not None
        or meta.overlap_studies is not None
    ):
        raise ValueError("Network requires complete single-cohort sponsor-drug metadata")
    if meta.total_matches is not None and meta.total_matches != meta.retrieved_studies:
        raise ValueError("Network retrieval totals must be complete")
    if (
        summary.included_studies + summary.excluded_studies != meta.retrieved_studies
        or summary.unnamed_drug_studies > meta.retrieved_studies
        or summary.displayed_nodes != len(chart.data.nodes)
        or summary.displayed_edges != len(chart.data.edges)
        or any(edge.weight > summary.included_studies for edge in chart.data.edges)
        or bool(summary.total_edges) != bool(summary.included_studies)
    ):
        raise ValueError("Graph metadata must agree with studies, nodes, edges, and weights")
    if (
        not summary.display_truncated
        and sum(edge.weight for edge in chart.data.edges) < summary.included_studies
    ):
        raise ValueError("Every included study must support at least one complete graph edge")


def _validate_histogram_metadata(chart: HistogramVisualization, meta: VisualizationMeta) -> None:
    summary = meta.histogram
    if (
        summary is None
        or meta.grouping != "enrollment_count"
        or meta.counting_unit != "unique NCT IDs per enrollment bin"
        or not meta.complete_results
        or meta.cohorts
        or meta.comparison_field is not None
        or meta.overlap_studies is not None
    ):
        raise ValueError("Histogram requires complete single-cohort enrollment metadata")
    if meta.total_matches is not None and meta.total_matches != meta.retrieved_studies:
        raise ValueError("Histogram retrieval totals must be complete")
    if (
        summary.included_studies + summary.excluded_studies != meta.retrieved_studies
        or sum(row.trial_count for row in chart.data) != summary.included_studies
        or bool(chart.data) != bool(summary.included_studies)
    ):
        raise ValueError("Histogram bins and included/excluded totals are inconsistent")


def _validate_cartesian_metadata(chart: Visualization, meta: VisualizationMeta) -> None:
    fields = {
        "phase": "phase",
        "start_year": "start_year",
        "overall_status": "overall_status",
        "sponsor_class": "sponsor_class",
        "country": "country",
        "intervention": "intervention_type",
    }
    if fields.get(meta.grouping) != chart.encoding.x.field or not meta.complete_results:
        raise ValueError("Metadata grouping/completeness must match the chart")
    if (meta.grouping == "start_year") != (chart.type == ChartType.TIME_SERIES):
        raise ValueError("Only annual charts may use start_year grouping")
    expected_unit = (
        "unique NCT IDs per category per cohort"
        if chart.type == ChartType.GROUPED_BAR
        else f"unique NCT IDs per {meta.grouping}"
    )
    if meta.counting_unit != expected_unit:
        raise ValueError("Counting unit must match the grouping and cohort semantics")
    if chart.type == ChartType.GROUPED_BAR:
        _validate_comparison_metadata(chart, meta)
    else:
        _validate_single_cohort_metadata(chart, meta)


def _validate_comparison_metadata(chart: Visualization, meta: VisualizationMeta) -> None:
    if len(meta.cohorts) != 2 or meta.comparison_field is None or meta.overlap_studies is None:
        raise ValueError("Comparisons require two cohorts and overlap metadata")
    if getattr(meta.filters, meta.comparison_field) is not None:
        raise ValueError("Shared filters cannot restrict the comparison axis")
    labels = [cohort.label for cohort in meta.cohorts]
    if len(set(labels)) != 2 or meta.total_matches is not None:
        raise ValueError("Cohort labels must differ; no combined population total is supplied")
    sizes = {cohort.label: cohort.retrieved_studies for cohort in meta.cohorts}
    if (
        meta.overlap_studies > min(sizes.values())
        or meta.retrieved_studies != sum(sizes.values()) - meta.overlap_studies
    ):
        raise ValueError("Distinct union and overlap counts are inconsistent")
    for cohort in meta.cohorts:
        if not cohort.complete_results or (
            cohort.total_matches is not None and cohort.total_matches != cohort.retrieved_studies
        ):
            raise ValueError("Each comparison cohort must be complete")
        expected = {**meta.filters.model_dump(), meta.comparison_field: cohort.label}
        if cohort.filters.model_dump() != expected:
            raise ValueError("Effective cohort filters must match labels and shared filters")
    aligned: dict[str, set[str]] = {}
    for row in chart.data:
        if row["cohort"] not in sizes or row["trial_count"] > sizes[row["cohort"]]:
            raise ValueError("Invalid cohort label or count")
        aligned.setdefault(row[chart.encoding.x.field], set()).add(row["cohort"])
    if any(values != set(labels) for values in aligned.values()):
        raise ValueError("Each category must have a row for each complete cohort")


def _validate_single_cohort_metadata(chart: Visualization, meta: VisualizationMeta) -> None:
    if (
        chart.type == ChartType.TIME_SERIES
        and sum(row["trial_count"] for row in chart.data) > meta.retrieved_studies
    ):
        raise ValueError("Annual counts cannot exceed distinct retrieved studies")
    if meta.cohorts or meta.comparison_field is not None or meta.overlap_studies is not None:
        raise ValueError("Single-cohort charts cannot have comparison metadata")
    if meta.total_matches is not None and meta.total_matches != meta.retrieved_studies:
        raise ValueError("Single-cohort retrieval must be complete")
    if any(row["trial_count"] > meta.retrieved_studies for row in chart.data):
        raise ValueError("Category count exceeds distinct retrieved studies")


class CitedVisualizationResponse(VisualizationResponse):
    """Explicit opt-in response; ordinary responses have no citations field."""

    citations: CitationBundle

    @model_validator(mode="after")
    def citation_coverage(self) -> Self:
        chart = self.visualization
        if isinstance(chart, NetworkVisualization):
            counts = {
                f"/visualization/data/edges/{i}": edge.weight
                for i, edge in enumerate(chart.data.edges)
            }
            cohorts = dict.fromkeys(counts)
            expected_nodes = {
                node.id: [
                    f"/visualization/data/edges/{i}"
                    for i, edge in enumerate(chart.data.edges)
                    if node.id in (edge.source, edge.target)
                ]
                for node in chart.data.nodes
            }
            actual_nodes = {node.node_id: node.edge_paths for node in self.citations.nodes}
            if len(actual_nodes) != len(self.citations.nodes) or actual_nodes != expected_nodes:
                raise ValueError("Every graph node must link exactly to its displayed cited edges")
        else:
            counts = {
                f"/visualization/data/{i}": row.trial_count
                if isinstance(chart, HistogramVisualization)
                else row["trial_count"]
                for i, row in enumerate(chart.data)
            }
            cohorts = (
                {f"/visualization/data/{i}": row.get("cohort") for i, row in enumerate(chart.data)}
                if isinstance(chart, Visualization)
                else dict.fromkeys(counts)
            )
            if self.citations.nodes:
                raise ValueError("Only graph responses have node citations")
        actual = {datum.datum_path: datum.supporting_study_count for datum in self.citations.datums}
        if actual != counts or list(actual) != list(counts):
            raise ValueError("Every displayed datum must have complete count-matched citations")
        if any(d.cohort != cohorts[d.datum_path] for d in self.citations.datums):
            raise ValueError("Citation cohort must agree with its chart row")
        return self
