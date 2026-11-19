"""Literal search constraints and public visualization request validation."""

from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from .base import StrictModel
from .enums import ChartType, TrialStatus

# Literal phrases only: prevent callers from injecting the registry query language.
SearchText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=200, pattern=r'^[^"\\\[\]{}\x00-\x1f\x7f]+$'
    ),
]
Year = Annotated[int, Field(strict=True, ge=1800, le=2100)]


class StudyFilters(StrictModel):
    drug_name: SearchText | None = None
    condition: SearchText | None = None
    country: SearchText | None = None
    sponsor: SearchText | None = None
    trial_status: TrialStatus | None = None
    start_year: Year | None = None
    end_year: Year | None = None

    @model_validator(mode="after")
    def ordered_years(self) -> Self:
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.start_year > self.end_year
        ):
            raise ValueError("start_year must not exceed end_year")
        return self


class VisualizationRequest(StudyFilters):
    include_citations: Annotated[
        bool,
        Field(strict=True, description="Include complete exact-source citations; default false."),
    ] = False
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    preferred_chart: ChartType | None = None
