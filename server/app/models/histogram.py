"""Fixed enrollment bins, encodings, and population summaries."""

from typing import Literal, Self

from pydantic import model_validator

from .base import NonNegativeInt, StrictModel
from .charts import AxisEncoding
from .enums import ChartType

# Enrollment counts are non-negative integers; upper bounds are exclusive.
ENROLLMENT_BINS = (
    (0, 10, "0–9"),
    (10, 50, "10–49"),
    (50, 100, "50–99"),
    (100, 500, "100–499"),
    (500, 1000, "500–999"),
    (1000, 5000, "1000–4999"),
    (5000, None, "5000+"),
)


class EnrollmentBin(StrictModel):
    bin_label: str
    bin_lower: NonNegativeInt
    bin_upper: NonNegativeInt | None
    trial_count: NonNegativeInt


class HistogramEncoding(StrictModel):
    x: AxisEncoding
    x2: AxisEncoding
    y: AxisEncoding

    @model_validator(mode="after")
    def bin_channels(self) -> Self:
        for channel, field in (
            (self.x, "bin_lower"),
            (self.x2, "bin_upper"),
            (self.y, "trial_count"),
        ):
            if channel.field != field or channel.type != "quantitative":
                raise ValueError(
                    "Histogram channels must encode numeric bin bounds and study counts"
                )
        return self


class HistogramVisualization(StrictModel):
    type: Literal[ChartType.HISTOGRAM] = ChartType.HISTOGRAM
    title: str
    encoding: HistogramEncoding
    data: list[EnrollmentBin]

    @model_validator(mode="after")
    def documented_bins(self) -> Self:
        if self.data and [
            (row.bin_lower, row.bin_upper, row.bin_label) for row in self.data
        ] != list(ENROLLMENT_BINS):
            raise ValueError("Histogram must use the documented ordered, non-overlapping bins")
        return self


class EnrollmentSummary(StrictModel):
    included_studies: NonNegativeInt
    excluded_studies: NonNegativeInt
    actual_studies: NonNegativeInt
    estimated_studies: NonNegativeInt
    unknown_type_studies: NonNegativeInt

    @model_validator(mode="after")
    def type_totals(self) -> Self:
        if (
            self.actual_studies + self.estimated_studies + self.unknown_type_studies
            != self.included_studies
        ):
            raise ValueError("Enrollment-type totals must equal included studies")
        return self
