"""Lead sponsor–drug support aggregation and bounded graph construction."""

from collections import Counter
from collections.abc import Sequence
from heapq import nsmallest

from app.aggregation import named_drugs, unique_studies
from app.clinical_trials import NormalizedStudy, RetrievalResult
from app.models import (
    GRAPH_EDGE_LIMIT,
    AxisEncoding,
    GraphEdge,
    GraphNode,
    GraphSummary,
    NetworkData,
    NetworkEncoding,
    NetworkVisualization,
    QueryPlan,
    VisualizationMeta,
    VisualizationResponse,
    graph_node_id,
)


def _display_graph(supports: Counter[tuple[str, str]]) -> NetworkData:
    displayed = nsmallest(
        GRAPH_EDGE_LIMIT, supports, key=lambda pair: (-supports[pair], pair[0], pair[1])
    )
    nodes = {
        graph_node_id(kind, label): GraphNode(
            id=graph_node_id(kind, label), entity_type=kind, label=label
        )
        for sponsor, drug in displayed
        for kind, label in (("sponsor", sponsor), ("drug", drug))
    }
    edges = [
        GraphEdge(
            source=graph_node_id("sponsor", sponsor),
            target=graph_node_id("drug", drug),
            weight=supports[sponsor, drug],
        )
        for sponsor, drug in displayed
    ]
    return NetworkData(nodes=[nodes[key] for key in sorted(nodes)], edges=edges)


def sponsor_drug_graph(studies: Sequence[NormalizedStudy]) -> tuple[NetworkData, GraphSummary]:
    supports: Counter[tuple[str, str]] = Counter()
    included = missing_sponsor = no_drug = unnamed_drug = 0
    records = unique_studies(studies)
    for study in records:
        drugs = named_drugs(study)
        if not study.sponsor_name:
            missing_sponsor += 1
        if not drugs:
            no_drug += 1
        if any(item.type == "DRUG" and not item.name for item in study.interventions):
            unnamed_drug += 1
        if not study.sponsor_name or not drugs:
            continue
        included += 1
        for drug in drugs:
            # IDs and names are deduplicated above: one increment per study/pair.
            supports[study.sponsor_name, drug] += 1
    all_entities = {
        (kind, label)
        for sponsor, drug in supports
        for kind, label in (("sponsor", sponsor), ("drug", drug))
    }
    data = _display_graph(supports)
    return data, GraphSummary(
        included_studies=included,
        excluded_studies=len(records) - included,
        missing_sponsor_studies=missing_sponsor,
        no_named_drug_studies=no_drug,
        unnamed_drug_studies=unnamed_drug,
        total_nodes=len(all_entities),
        total_edges=len(supports),
        displayed_nodes=len(data.nodes),
        displayed_edges=len(data.edges),
        omitted_nodes=len(all_entities) - len(data.nodes),
        omitted_edges=len(supports) - len(data.edges),
        display_truncated=len(supports) > len(data.edges),
    )


def build_network(plan: QueryPlan, result: RetrievalResult) -> VisualizationResponse:
    data, summary = sponsor_drug_graph(result.studies)
    warnings = [
        *result.warnings,
        "Edges represent lead sponsor and DRUG intervention co-occurrence "
        "within registered studies, "
        "not commercial partnerships, efficacy, or causal relationships.",
        "Only reported DRUG interventions are included; BIOLOGICAL and other types are excluded. "
        "Labels are matched exactly without entity resolution. A study may support several edges.",
    ]
    if summary.excluded_studies:
        warnings.append(
            f"{summary.excluded_studies} studies have no eligible sponsor-drug edge; "
            "missing-sponsor and no-named-drug diagnostics may overlap."
        )
    if summary.unnamed_drug_studies:
        warnings.append(
            f"{summary.unnamed_drug_studies} studies have unnamed DRUG interventions, "
            "which are skipped even if other named drugs contribute edges."
        )
    if summary.display_truncated:
        warnings.append(
            "Graph display is limited to the 200 highest-weight edges, ties ordered by "
            "exact sponsor and drug labels. Omitted nodes/edges are disclosed; "
            "the study population retrieval is complete."
        )
    if plan.filters.sponsor:
        warnings.append(
            "Sponsor search includes collaborators; graph nodes always use the lead sponsor."
        )
    if plan.filters.drug_name:
        warnings.append(
            "Intervention search selects studies, not graph nodes; matching labels may "
            "be typed BIOLOGICAL and therefore absent from the graph."
        )

    def channel(field: str, title: str, quantitative: bool = False) -> AxisEncoding:
        return AxisEncoding(
            field=field, title=title, type="quantitative" if quantitative else "nominal"
        )

    return VisualizationResponse(
        visualization=NetworkVisualization(
            title="Lead sponsor–drug co-occurrence in registered studies",
            encoding=NetworkEncoding(
                node_id=channel("id", "Stable node ID"),
                node_label=channel("label", "Reported entity label"),
                node_type=channel("entity_type", "Sponsor or drug"),
                source=channel("source", "Lead sponsor node ID"),
                target=channel("target", "Drug node ID"),
                weight=channel("weight", "Distinct supporting studies", True),
            ),
            data=data,
        ),
        meta=VisualizationMeta(
            filters=plan.filters,
            grouping="sponsor_drug",
            counting_unit="unique NCT IDs per lead sponsor-drug edge",
            retrieved_studies=len(unique_studies(result.studies)),
            total_matches=result.total_matches,
            complete_results=result.complete_results,
            warnings=warnings,
            graph=summary,
        ),
    )
