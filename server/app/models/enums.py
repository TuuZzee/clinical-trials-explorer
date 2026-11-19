"""Allowlisted query, grouping, status, and chart vocabulary."""

from enum import StrEnum


class TrialStatus(StrEnum):
    NOT_YET_RECRUITING = "NOT_YET_RECRUITING"
    RECRUITING = "RECRUITING"
    ENROLLING_BY_INVITATION = "ENROLLING_BY_INVITATION"
    ACTIVE_NOT_RECRUITING = "ACTIVE_NOT_RECRUITING"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
    AVAILABLE = "AVAILABLE"
    NO_LONGER_AVAILABLE = "NO_LONGER_AVAILABLE"
    TEMPORARILY_NOT_AVAILABLE = "TEMPORARILY_NOT_AVAILABLE"
    APPROVED_FOR_MARKETING = "APPROVED_FOR_MARKETING"
    WITHHELD = "WITHHELD"
    UNKNOWN = "UNKNOWN"


class ChartType(StrEnum):
    BAR = "bar_chart"
    TIME_SERIES = "time_series"
    GROUPED_BAR = "grouped_bar_chart"
    HISTOGRAM = "histogram"
    NETWORK = "network_graph"


class QueryIntent(StrEnum):
    TIME_TREND = "time_trend"
    DISTRIBUTION = "distribution"
    COMPARISON = "comparison"
    GEOGRAPHIC = "geographic"
    RELATIONSHIP = "relationship"


class Measure(StrEnum):
    STUDY_COUNT = "study_count"


class Grouping(StrEnum):
    START_YEAR = "start_year"
    PHASE = "phase"
    STATUS = "overall_status"
    SPONSOR_CLASS = "sponsor_class"
    COUNTRY = "country"
    INTERVENTION = "intervention"
    ENROLLMENT = "enrollment_count"
    SPONSOR_DRUG = "sponsor_drug"


class ComparisonField(StrEnum):
    DRUG = "drug_name"
    CONDITION = "condition"


class PlanningStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    NEEDS_CLARIFICATION = "needs_clarification"
