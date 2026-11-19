import pytest
from pydantic import ValidationError

from app.charts import build_chart
from app.clinical_trials import Intervention, NormalizedStudy
from app.errors import ServiceError
from app.extra_charts import sponsor_drug_graph
from app.schemas import GraphEdge, QueryPlan, VisualizationResponse, graph_node_id
from tests.helpers.charts import retrieval


def network_plan(**overrides):
    return QueryPlan.model_validate(
        {
            "intent": "relationship",
            "measure": "study_count",
            "filters": {},
            "group_by": "sponsor_drug",
            "chart_type": "network_graph",
            "comparison": None,
            **overrides,
        }
    )


def graph_study(index, sponsor, interventions):
    return NormalizedStudy(
        nct_id=f"NCT{index:08d}",
        sponsor_name=sponsor,
        interventions=tuple(Intervention(name=name, type=kind) for name, kind in interventions),
    )


def edge_labels(data):
    labels = {node.id: node.label for node in data.nodes}
    return {(labels[edge.source], labels[edge.target]): edge.weight for edge in data.edges}


def test_sponsor_drug_edges_distinct_support_and_exact_labels():
    first = graph_study(
        1,
        "Same name",
        [
            ("Same name", "DRUG"),
            ("Same name", "DRUG"),
            ("Drug B", "DRUG"),
            ("Biological C", "BIOLOGICAL"),
        ],
    )
    second = graph_study(2, "Same name", [("Same name", "DRUG")])
    third = graph_study(3, "Sponsor B", [("Same name", "DRUG")])
    data, summary = sponsor_drug_graph([first, first, second, third])
    assert edge_labels(data) == {
        ("Same name", "Same name"): 2,
        ("Same name", "Drug B"): 1,
        ("Sponsor B", "Same name"): 1,
    }
    assert summary.included_studies == 3 and summary.excluded_studies == 0
    assert len({node.id for node in data.nodes}) == 4
    assert {node.entity_type for node in data.nodes if node.label == "Same name"} == {
        "drug",
        "sponsor",
    }
    assert all(node.id.startswith(node.entity_type + ":") for node in data.nodes)
    assert data.edges[0].weight == 2
    assert "Biological C" not in {node.label for node in data.nodes}
    assert sponsor_drug_graph([third, second, first]) == (data, summary)


def test_graph_exclusion_counts_allow_overlapping_missing_reasons():
    studies = [
        graph_study(1, "S", [("A", "DRUG")]),
        graph_study(2, None, [("A", "DRUG")]),
        graph_study(3, "S", []),
        graph_study(4, None, [(None, "DRUG")]),
        graph_study(5, "S", [(None, "DRUG"), ("B", "DRUG")]),
        graph_study(6, "S", [("C", "BIOLOGICAL")]),
    ]
    response = build_chart(network_plan(), [retrieval(studies)])
    summary = response.meta.graph
    assert summary.included_studies == 2 and summary.excluded_studies == 4
    assert summary.missing_sponsor_studies == 2 and summary.no_named_drug_studies == 3
    assert summary.unnamed_drug_studies == 2
    assert edge_labels(response.visualization.data) == {("S", "A"): 1, ("S", "B"): 1}
    assert response.meta.retrieved_studies == 6 and response.meta.complete_results
    assert any("not commercial partnerships" in w for w in response.meta.warnings)
    assert any("diagnostics may overlap" in w for w in response.meta.warnings)


def test_graph_duplicates_retain_first_record_without_inventing_relationships():
    studies = [graph_study(1, "S", [("A", "DRUG")]), graph_study(1, "Other", [("B", "DRUG")])]
    data, summary = sponsor_drug_graph(studies)
    assert edge_labels(data) == {("S", "A"): 1}
    assert summary.included_studies == 1


@pytest.mark.parametrize(
    "studies", [[], [graph_study(1, None, [])], [graph_study(1, "S", [("B", "BIOLOGICAL")])]]
)
def test_graph_empty_or_ineligible_studies_have_no_nodes_or_edges(studies):
    response = build_chart(network_plan(), [retrieval(studies)])
    assert response.visualization.data.nodes == response.visualization.data.edges == []
    assert response.meta.graph.included_studies == 0
    assert response.meta.graph.excluded_studies == len(studies)
    assert not response.meta.graph.display_truncated


def test_graph_cap_is_display_only_stable_and_disclosed():
    studies = [graph_study(i, f"Sponsor {i:03d}", [("Drug", "DRUG")]) for i in range(201)]
    response = build_chart(network_plan(), [retrieval(studies)])
    data, summary = response.visualization.data, response.meta.graph
    assert summary.total_edges == 201 and summary.displayed_edges == 200
    assert summary.total_nodes == 202 and summary.displayed_nodes == 201
    assert summary.omitted_edges == summary.omitted_nodes == 1
    assert summary.display_truncated and response.meta.complete_results
    assert response.meta.retrieved_studies == 201
    assert ("Sponsor 200", "Drug") not in edge_labels(data)
    assert sponsor_drug_graph(studies) == sponsor_drug_graph(list(reversed(studies)))
    assert any("display is limited" in w for w in response.meta.warnings)


@pytest.mark.parametrize("weight", [True, False, 0, -1, 1.0, "1"])
def test_graph_edge_weights_are_strict_positive_counts(weight):
    with pytest.raises(ValidationError):
        GraphEdge(source="sponsor:x", target="drug:y", weight=weight)


@pytest.mark.parametrize(
    "mutation",
    [
        "identity",
        "endpoint",
        "direction",
        "duplicate_edge",
        "orphan",
        "weight",
        "counts",
        "omissions",
        "encoding",
        "grouping",
        "histogram_meta",
    ],
)
def test_network_contract_rejects_invalid_graph(mutation):
    output = build_chart(
        network_plan(), [retrieval([graph_study(1, "S", [("D", "DRUG")])])]
    ).model_dump(mode="json")
    chart, summary = output["visualization"], output["meta"]["graph"]
    if mutation == "identity":
        chart["data"]["nodes"][0]["id"] = "invalid"
    elif mutation == "endpoint":
        chart["data"]["edges"][0]["target"] = "drug:absent"
    elif mutation == "direction":
        edge = chart["data"]["edges"][0]
        edge["source"], edge["target"] = edge["target"], edge["source"]
    elif mutation == "duplicate_edge":
        chart["data"]["edges"].append(dict(chart["data"]["edges"][0]))
    elif mutation == "orphan":
        chart["data"]["nodes"].append(
            {"id": graph_node_id("drug", "Other"), "label": "Other", "entity_type": "drug"}
        )
    elif mutation == "weight":
        chart["data"]["edges"][0]["weight"] = 2
    elif mutation == "counts":
        summary["included_studies"] = 2
    elif mutation == "omissions":
        summary["omitted_edges"] = 1
    elif mutation == "encoding":
        chart["encoding"]["node_label"]["field"] = "name"
    elif mutation == "grouping":
        output["meta"]["grouping"] = "intervention"
    else:
        output["meta"]["histogram"] = {
            "included_studies": 1,
            "excluded_studies": 0,
            "actual_studies": 1,
            "estimated_studies": 0,
            "unknown_type_studies": 0,
        }
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


def test_graph_requires_complete_population():
    with pytest.raises(ServiceError) as exc:
        build_chart(network_plan(), [retrieval([], complete_results=False)])
    assert exc.value.code == "incomplete_retrieval"


def test_graph_summary_rejects_fictitious_omitted_nodes_without_edges():
    output = build_chart(network_plan(), [retrieval([])]).model_dump(mode="json")
    output["meta"]["graph"].update(total_nodes=1, omitted_nodes=1, display_truncated=True)
    with pytest.raises(ValidationError):
        VisualizationResponse.model_validate(output)


def test_cartesian_chart_rejects_graph_type():
    from app.schemas import Visualization

    with pytest.raises(ValidationError):
        Visualization(
            type="network_graph",
            title="Invalid old shape",
            encoding={
                "x": {"field": "phase", "title": "Phase", "type": "ordinal"},
                "y": {"field": "trial_count", "title": "Count", "type": "quantitative"},
            },
            data=[],
        )


def test_graph_weight_priority_wins_over_label_order_at_display_limit():
    studies = [graph_study(i, f"Sponsor {i:03d}", [("Drug", "DRUG")]) for i in range(200)]
    studies += [
        graph_study(1000, "ZZZ", [("Drug", "DRUG"), ("Drug", "DRUG")]),
        graph_study(1001, "ZZZ", [("Drug", "DRUG")]),
    ]
    data, summary = sponsor_drug_graph(studies)
    assert edge_labels(data)["ZZZ", "Drug"] == 2
    assert ("Sponsor 199", "Drug") not in edge_labels(data)
    assert data.edges[0].weight == 2 and summary.total_edges == 201
    assert summary.included_studies == 202 and summary.display_truncated
