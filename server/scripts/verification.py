"""Independent set-based reference calculations for opt-in example verification.

These deliberately do not call the application's aggregation or chart builders.
The registry adapter and normalizer are shared; this is not a second API parser.
"""

from collections import defaultdict

from app.clinical_trials import NormalizedStudy, RetrievalResult
from app.models import HistogramVisualization, NetworkVisualization, VisualizationResponse


def verify_histogram(output: VisualizationResponse, records: list[NormalizedStudy]) -> dict:
    if not isinstance(output.visualization, HistogramVisualization):
        raise ValueError("Independent registry calculation disagrees with the response")
    boundaries = [(0, 10), (10, 50), (50, 100), (100, 500), (500, 1000), (1000, 5000), (5000, None)]
    counted = [set() for _ in boundaries]
    typed = {"ACTUAL": 0, "ESTIMATED": 0, "UNKNOWN": 0}
    excluded = 0
    for study in records:
        count = study.enrollment_count
        if count is None:
            excluded += 1
            continue
        for index, (lower, upper) in enumerate(boundaries):
            if lower <= count and (upper is None or count < upper):
                counted[index].add(study.nct_id)
                break
        typed[study.enrollment_type if study.enrollment_type in typed else "UNKNOWN"] += 1
    counts = [len(ids) for ids in counted]
    if sum(counts) <= 0:
        raise ValueError("Independent registry calculation disagrees with the response")
    if [row.trial_count for row in output.visualization.data] != counts:
        raise ValueError("Independent registry calculation disagrees with the response")
    summary = output.meta.histogram
    if summary is None:
        raise ValueError("Independent registry calculation disagrees with the response")
    if summary.model_dump() != {
        "included_studies": sum(counts),
        "excluded_studies": excluded,
        "actual_studies": typed["ACTUAL"],
        "estimated_studies": typed["ESTIMATED"],
        "unknown_type_studies": typed["UNKNOWN"],
    }:
        raise ValueError("Independent registry calculation disagrees with the response")
    return {"bin_counts": counts, "enrollment_summary": summary.model_dump()}


def verify_graph(output: VisualizationResponse, records: list[NormalizedStudy]) -> dict:
    if not isinstance(output.visualization, NetworkVisualization):
        raise ValueError("Independent registry calculation disagrees with the response")
    expected: dict[tuple[str, str], set[str]] = defaultdict(set)
    included = missing_sponsor = no_drug = unnamed = 0
    for study in records:
        drugs = {item.name for item in study.interventions if item.type == "DRUG" and item.name}
        missing_sponsor += int(not study.sponsor_name)
        no_drug += int(not drugs)
        unnamed += int(any(item.type == "DRUG" and (not item.name) for item in study.interventions))
        if study.sponsor_name and drugs:
            included += 1
            for name in drugs:
                expected[study.sponsor_name, name].add(study.nct_id)
    ranked = sorted(expected, key=lambda pair: (-len(expected[pair]), pair))
    selected = ranked[:200]
    if not selected:
        raise ValueError("Independent registry calculation disagrees with the response")
    data = output.visualization.data
    labels = {node.id: node.label for node in data.nodes}
    actual = {(labels[edge.source], labels[edge.target]): edge.weight for edge in data.edges}
    if actual != {pair: len(expected[pair]) for pair in selected}:
        raise ValueError("Independent registry calculation disagrees with the response")
    all_nodes = {
        (kind, name)
        for sponsor, drug in ranked
        for kind, name in [("sponsor", sponsor), ("drug", drug)]
    }
    displayed_nodes = {
        (kind, name)
        for sponsor, drug in selected
        for kind, name in [("sponsor", sponsor), ("drug", drug)]
    }
    if {(node.entity_type, node.label) for node in data.nodes} != displayed_nodes:
        raise ValueError("Independent registry calculation disagrees with the response")
    summary = output.meta.graph
    if summary is None:
        raise ValueError("Independent registry calculation disagrees with the response")
    if summary.model_dump() != {
        "included_studies": included,
        "excluded_studies": len(records) - included,
        "missing_sponsor_studies": missing_sponsor,
        "no_named_drug_studies": no_drug,
        "unnamed_drug_studies": unnamed,
        "total_nodes": len(all_nodes),
        "total_edges": len(ranked),
        "displayed_nodes": len(displayed_nodes),
        "displayed_edges": len(selected),
        "omitted_nodes": len(all_nodes) - len(displayed_nodes),
        "omitted_edges": len(ranked) - len(selected),
        "display_truncated": len(ranked) > len(selected),
        "edge_limit": 200,
    }:
        raise ValueError("Independent registry calculation disagrees with the response")
    return {"graph_summary": summary.model_dump(), "max_edge_weight": max(actual.values())}


PHASES = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
}


def reference_counts(studies: list[NormalizedStudy], grouping: str) -> dict[str, int]:
    buckets: dict[str, set[str]] = defaultdict(set)
    for study in studies:
        if grouping == "start_year":
            values = [f"{study.start_year:04d}-01-01"] if study.start_year is not None else []
        elif grouping == "phase":
            values = [PHASES.get(p, f"Unrecognized phase: {p}") for p in study.phases]
            values = values or ["Missing phase"]
        elif grouping == "overall_status":
            values = [study.overall_status or "Missing status"]
        elif grouping == "sponsor_class":
            values = [study.sponsor_class or "Missing lead sponsor class"]
        elif grouping == "country":
            values = list(study.countries) or ["Missing country"]
        elif grouping == "intervention":
            values = [i.type or "Missing intervention type" for i in study.interventions]
            values = values or ["Missing intervention type"]
        else:
            raise ValueError("This example has no independent reference calculation")
        for value in values:
            buckets[value].add(study.nct_id)
    return {label: len(ids) for label, ids in buckets.items()}


def verify_results(output: VisualizationResponse, results: list[RetrievalResult]) -> dict:
    """Verify completeness, population metadata, and independently calculated values."""
    expected = 2 if output.meta.cohorts else 1
    if len(results) != expected:
        raise ValueError("One independent retrieval is required per cohort")
    ids = [{study.nct_id for study in result.studies} for result in results]
    for result, members in zip(results, ids, strict=True):
        if not result.complete_results or (
            result.total_matches is not None and result.total_matches != len(members)
        ):
            raise ValueError("Independent retrieval must be complete and internally consistent")
    if not output.meta.complete_results or output.meta.retrieved_studies != len(set().union(*ids)):
        raise ValueError("Distinct population metadata disagrees")
    if output.meta.cohorts:
        for index, cohort in enumerate(output.meta.cohorts):
            if (
                not cohort.complete_results
                or cohort.retrieved_studies != len(ids[index])
                or cohort.total_matches != results[index].total_matches
            ):
                raise ValueError("Cohort retrieval metadata disagrees")
        if output.meta.overlap_studies != len(ids[0] & ids[1]):
            raise ValueError("Comparison overlap disagrees")
    elif output.meta.total_matches != results[0].total_matches:
        raise ValueError("Registry population total disagrees")
    if isinstance(output.visualization, HistogramVisualization):
        return verify_histogram(output, results[0].studies)
    if isinstance(output.visualization, NetworkVisualization):
        return verify_graph(output, results[0].studies)
    counts = [reference_counts(result.studies, output.meta.grouping) for result in results]
    field = output.visualization.encoding.x.field
    if output.meta.cohorts:
        labels = [cohort.label for cohort in output.meta.cohorts]
        categories = set().union(*(set(count) for count in counts))
        expected_counts = {
            (category, label): counts[index].get(category, 0)
            for category in categories
            for index, label in enumerate(labels)
        }
        actual = {
            (row[field], row["cohort"]): row["trial_count"] for row in output.visualization.data
        }
    else:
        expected_counts = counts[0]
        actual = {row[field]: row["trial_count"] for row in output.visualization.data}
    if actual != expected_counts:
        raise ValueError("Independent category counts or cohort alignment disagree")
    return {
        "cohort_sizes": [len(members) for members in ids],
        "overlap_studies": output.meta.overlap_studies,
        "chart_rows": len(actual),
        "zero_rows": sum(count == 0 for count in actual.values()),
    }
