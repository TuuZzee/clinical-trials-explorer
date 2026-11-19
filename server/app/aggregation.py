"""Distinct-study category and annual count aggregation with stable ordering."""

from collections import defaultdict
from collections.abc import Sequence

from app.models import ENROLLMENT_BINS, Grouping, PhaseCount
from app.normalization import NormalizedStudy

# Insertion order defines the clinical phase order; only observed buckets are emitted.
PHASE_LABELS = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
}
MISSING_PHASE = "Missing phase"


# group_by -> (row field, axis title, explicit missing label)
CATEGORIES = {
    Grouping.PHASE: ("phase", "Clinical trial phase", MISSING_PHASE),
    Grouping.STATUS: ("overall_status", "Study status", "Missing status"),
    Grouping.SPONSOR_CLASS: ("sponsor_class", "Lead sponsor class", "Missing lead sponsor class"),
    Grouping.INTERVENTION: ("intervention_type", "Intervention type", "Missing intervention type"),
    Grouping.COUNTRY: ("country", "Trial location country", "Missing country"),
}


def unique_studies(studies: Sequence[NormalizedStudy]) -> list[NormalizedStudy]:
    """Use the adapter's first-record-wins policy for duplicate IDs."""
    records = {}
    for study in studies:
        records.setdefault(study.nct_id, study)
    return list(records.values())


def _phases_by_study(studies: Sequence[NormalizedStudy]) -> dict[str, set[str]]:
    phases: dict[str, set[str]] = defaultdict(set)
    for study in studies:
        phases[study.nct_id].update(study.phases)
    return dict(phases)


def aggregate_phases(studies: Sequence[NormalizedStudy]) -> list[PhaseCount]:
    """Count each NCT ID once per observed category; missing is distinct from NA."""
    counts: dict[str, int] = defaultdict(int)
    for labels in category_values_by_study(studies, Grouping.PHASE).values():
        for label in labels:
            counts[label] += 1
    known_labels = list(PHASE_LABELS.values())
    order = {label: index for index, label in enumerate(known_labels)}
    labels = sorted(
        counts, key=lambda label: (order.get(label, len(order) + (label == MISSING_PHASE)), label)
    )
    return [PhaseCount(phase=label, trial_count=counts[label]) for label in labels]


def _phase_warnings(phases: dict[str, set[str]]) -> list[str]:
    warnings = []
    missing = sum(not values for values in phases.values())
    if missing:
        warnings.append(f"{missing} studies have no reported phase and appear as 'Missing phase'.")
    multi_phase = sum(len(values) > 1 for values in phases.values())
    if multi_phase:
        warnings.append(
            f"{multi_phase} studies contribute to multiple phase categories; "
            "the sum of phase counts may exceed the number of unique retrieved studies."
        )
    if any(values - PHASE_LABELS.keys() for values in phases.values()):
        warnings.append("Unrecognized phase values are preserved in separately labeled categories.")
    return warnings


def _category_order(label: str, grouping: Grouping) -> tuple[int, str]:
    if grouping == Grouping.PHASE:
        order = {label: index for index, label in enumerate(PHASE_LABELS.values())}
        return order.get(label, len(order) + (label == MISSING_PHASE)), label
    return int(label == CATEGORIES[grouping][2]), label


def category_values_by_study(
    studies: Sequence[NormalizedStudy], grouping: Grouping
) -> dict[str, set[str]]:
    """Union categories per ID for direct builders; adapter inputs are first-record-wins."""
    if grouping == Grouping.PHASE:
        phases = _phases_by_study(studies)
        for nct_id, values in phases.items():
            phases[nct_id] = {PHASE_LABELS.get(p, f"Unrecognized phase: {p}") for p in values} or {
                MISSING_PHASE
            }
        return phases
    missing = CATEGORIES[grouping][2]
    categories: dict[str, set[str]] = defaultdict(set)
    for study in studies:
        if grouping == Grouping.STATUS:
            values = {study.overall_status} if study.overall_status else set()
        elif grouping == Grouping.SPONSOR_CLASS:
            values = {study.sponsor_class} if study.sponsor_class else set()
        elif grouping == Grouping.INTERVENTION:
            values = {item.type or missing for item in study.interventions}
        else:
            values = set(study.countries)
        categories[study.nct_id].update(values)
    for values in categories.values():
        if not values:
            values.add(missing)
    return dict(categories)


def _categorical_counts(
    studies: Sequence[NormalizedStudy], grouping: Grouping
) -> tuple[dict[str, int], list[str]]:
    if grouping == Grouping.PHASE:
        # Phase categories preserve missing values and multi-phase membership.
        counts = {row.phase: row.trial_count for row in aggregate_phases(studies)}
        return counts, _phase_warnings(_phases_by_study(studies))
    missing = CATEGORIES[grouping][2]
    counts: dict[str, int] = defaultdict(int)
    for values in category_values_by_study(studies, grouping).values():
        for value in values:
            counts[value] += 1
    warnings = []
    if counts.get(missing):
        warnings.append(f"{counts[missing]} studies contribute to '{missing}'.")
    if grouping in {Grouping.COUNTRY, Grouping.INTERVENTION}:
        warnings.append(
            "Studies count once per category and may appear in multiple categories; "
            "category totals are not mutually exclusive."
        )
    if grouping == Grouping.COUNTRY:
        warnings.append("Country means reported trial locations, not sponsor country.")
    return dict(counts), warnings


def _annual_counts(studies: Sequence[NormalizedStudy]) -> tuple[dict[str, int], list[str]]:
    counts: dict[str, int] = defaultdict(int)
    omitted = 0
    for study in unique_studies(studies):
        bucket = annual_bucket(study)
        if bucket is None:
            omitted += 1
        else:
            counts[bucket] += 1
    warnings = [
        "Annual values refer to study start dates, not posting or completion dates; "
        "only observed years are emitted."
    ]
    if omitted:
        warnings.append(
            f"{omitted} studies excluded from annual counts due to missing or invalid start dates."
        )
    return dict(counts), warnings


def annual_bucket(study: NormalizedStudy) -> str | None:
    year = study.start_year
    return f"{year:04d}-01-01" if year is not None and 1 <= year <= 9999 else None


def enrollment_bin(value: int | None) -> int | None:
    if value is None:
        return None
    for index, (lower, upper, _) in enumerate(ENROLLMENT_BINS):
        if lower <= value and (upper is None or value < upper):
            return index
    return None


def named_drugs(study: NormalizedStudy) -> set[str]:
    return {item.name for item in study.interventions if item.type == "DRUG" and item.name}
