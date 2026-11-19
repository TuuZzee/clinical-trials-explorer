"""Cartesian encodings, rows, and chart contracts."""

from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from .base import StrictModel
from .enums import ChartType


class AxisEncoding(StrictModel):
    field: str
    type: Literal["nominal", "ordinal", "quantitative", "temporal"]
    title: str


class Encoding(StrictModel):
    x: AxisEncoding
    y: AxisEncoding
    series: AxisEncoding | None = None


class PhaseCount(StrictModel):
    phase: Annotated[str, StringConstraints(min_length=1)]
    trial_count: Annotated[int, Field(strict=True, ge=1)]


class Visualization(StrictModel):
    """Library-independent count chart with strict row and encoding validation."""

    type: Literal[ChartType.BAR, ChartType.TIME_SERIES, ChartType.GROUPED_BAR]
    title: str
    encoding: Encoding
    data: list[dict[str, str | int | float | None]]

    @model_validator(mode="before")
    @classmethod
    def strict_counts(cls, value: Any) -> Any:
        if isinstance(value, dict):
            rows = value.get("data", [])
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and (
                    type(row.get("trial_count")) is not int or row["trial_count"] < 0
                ):
                    raise ValueError("trial_count must be a non-negative JSON integer")
        return value

    @model_validator(mode="after")
    def consistent_rows(self) -> Self:
        x, y, series = self.encoding.x, self.encoding.y, self.encoding.series
        if y.field != "trial_count" or y.type != "quantitative":
            raise ValueError("Y must encode quantitative trial_count")
        if (self.type == ChartType.GROUPED_BAR) != (series is not None):
            raise ValueError("Only grouped bars require a series encoding")
        if series and (series.field != "cohort" or series.type != "nominal"):
            raise ValueError("Series must encode nominal cohort labels")
        if self.type == ChartType.TIME_SERIES:
            if x.field != "start_year" or x.type != "temporal":
                raise ValueError("Time series must encode temporal start_year")
        elif x.type not in {"nominal", "ordinal"}:
            raise ValueError("Bars must have a categorical X axis")
        fields = {x.field, y.field} | ({series.field} if series else set())
        if len(fields) != (3 if series else 2):
            raise ValueError("Encoding fields must be distinct")
        seen = set()
        for row in self.data:
            if set(row) != fields or type(row[y.field]) is not int:
                raise ValueError("Data rows must match encoded fields with integer counts")
            if not isinstance(row[x.field], str) or not row[x.field]:
                raise ValueError("X values must be nonempty strings")
            if self.type == ChartType.TIME_SERIES:
                text = row[x.field]
                if len(text) != 10 or not text.endswith("-01-01"):
                    raise ValueError("Annual temporal values must use YYYY-01-01")
                date.fromisoformat(text)
            if series and (not isinstance(row[series.field], str) or not row[series.field]):
                raise ValueError("Cohort labels must be nonempty strings")
            identity = (row[x.field], row[series.field] if series else None)
            if identity in seen:
                raise ValueError("Duplicate category/cohort row")
            seen.add(identity)
        if self.type == ChartType.TIME_SERIES:
            years = [row[x.field] for row in self.data]
            if years != sorted(years):
                raise ValueError("Annual rows must be chronological")
        return self
