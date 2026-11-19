"""Constrained query plans, comparisons, and structured-output envelopes."""

from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from .base import StrictModel
from .enums import ChartType, ComparisonField, Grouping, Measure, PlanningStatus, QueryIntent
from .requests import SearchText, StudyFilters


class Comparison(StrictModel):
    field: ComparisonField
    values: Annotated[list[SearchText], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def distinct_cohorts(self) -> Self:
        if len({" ".join(value.casefold().split()) for value in self.values}) != 2:
            raise ValueError("Comparison requires two distinct search phrases")
        return self


class QueryPlan(StrictModel):
    """Registry-count interpretation only; contains neither data nor API expressions."""

    intent: QueryIntent
    measure: Measure
    filters: StudyFilters
    group_by: Grouping
    chart_type: ChartType
    comparison: Comparison | None

    @model_validator(mode="after")
    def supported_combination(self) -> Self:
        categorical = {
            Grouping.PHASE,
            Grouping.STATUS,
            Grouping.SPONSOR_CLASS,
            Grouping.INTERVENTION,
        }
        valid = {
            QueryIntent.TIME_TREND: self.group_by == Grouping.START_YEAR
            and self.chart_type == ChartType.TIME_SERIES,
            QueryIntent.DISTRIBUTION: (
                self.group_by in categorical and self.chart_type == ChartType.BAR
            )
            or (self.group_by == Grouping.ENROLLMENT and self.chart_type == ChartType.HISTOGRAM),
            QueryIntent.GEOGRAPHIC: self.group_by == Grouping.COUNTRY
            and self.chart_type == ChartType.BAR,
            QueryIntent.RELATIONSHIP: self.group_by == Grouping.SPONSOR_DRUG
            and self.chart_type == ChartType.NETWORK,
            QueryIntent.COMPARISON: self.group_by in categorical | {Grouping.COUNTRY}
            and self.chart_type == ChartType.GROUPED_BAR,
        }[self.intent]
        has_comparison = self.comparison is not None
        if not valid or has_comparison != (self.intent == QueryIntent.COMPARISON):
            raise PydanticCustomError(
                "unsupported_plan", "Unsupported intent/grouping/chart combination"
            )
        if self.comparison and getattr(self.filters, self.comparison.field.value) is not None:
            raise PydanticCustomError(
                "unsupported_plan", "Shared filters conflict with comparison axis"
            )
        return self

    def cohort_filters(self) -> list[StudyFilters]:
        """Prepare independent searches; comparison membership may overlap."""
        if self.comparison is None:
            return [StudyFilters.model_validate(self.filters.model_dump())]
        return [
            StudyFilters.model_validate(
                {**self.filters.model_dump(), self.comparison.field.value: value}
            )
            for value in self.comparison.values
        ]


class PlanningResult(StrictModel):
    """Structured-output envelope provides an explicit path for unanswerable inputs."""

    status: PlanningStatus
    plan: QueryPlan | None

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if (self.status == PlanningStatus.SUPPORTED) != (self.plan is not None):
            raise ValueError("Only supported results must contain a plan")
        return self
