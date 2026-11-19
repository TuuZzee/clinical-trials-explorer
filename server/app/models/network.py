"""Stable graph identities, weighted edges, and display summaries."""

from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from .base import NonNegativeInt, StrictModel
from .charts import AxisEncoding
from .enums import ChartType

GRAPH_EDGE_LIMIT = 200


def graph_node_id(entity_type: str, label: str) -> str:
    return f"{entity_type}:{sha256(label.encode('utf-8')).hexdigest()}"


class GraphNode(StrictModel):
    id: str
    label: Annotated[str, StringConstraints(min_length=1)]
    entity_type: Literal["sponsor", "drug"]

    @model_validator(mode="after")
    def stable_identity(self) -> Self:
        if self.id != graph_node_id(self.entity_type, self.label):
            raise ValueError("Node identity must be the type-prefixed SHA-256 of its exact label")
        return self


class GraphEdge(StrictModel):
    source: str
    target: str
    weight: Annotated[int, Field(strict=True, ge=1)]


class NetworkData(StrictModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @model_validator(mode="after")
    def bipartite_graph(self) -> Self:
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes) or list(nodes) != sorted(nodes):
            raise ValueError("Nodes must be unique and ordered by ID")
        pairs = set()
        connected = set()
        for edge in self.edges:
            if (
                edge.source not in nodes
                or edge.target not in nodes
                or nodes[edge.source].entity_type != "sponsor"
                or nodes[edge.target].entity_type != "drug"
            ):
                raise ValueError("Edges must connect an existing sponsor to an existing drug")
            pair = (edge.source, edge.target)
            if pair in pairs:
                raise ValueError("Duplicate edge")
            pairs.add(pair)
            connected.update(pair)
        if connected != set(nodes):
            raise ValueError("Only nodes connected by displayed edges are included")
        if self.edges != sorted(
            self.edges,
            key=lambda edge: (-edge.weight, nodes[edge.source].label, nodes[edge.target].label),
        ):
            raise ValueError("Edges must be ordered by weight descending, then exact source labels")
        return self


class NetworkEncoding(StrictModel):
    node_id: AxisEncoding
    node_label: AxisEncoding
    node_type: AxisEncoding
    source: AxisEncoding
    target: AxisEncoding
    weight: AxisEncoding

    @model_validator(mode="after")
    def graph_channels(self) -> Self:
        for name, field in (
            ("node_id", "id"),
            ("node_label", "label"),
            ("node_type", "entity_type"),
            ("source", "source"),
            ("target", "target"),
            ("weight", "weight"),
        ):
            channel = getattr(self, name)
            if channel.field != field or channel.type != (
                "quantitative" if name == "weight" else "nominal"
            ):
                raise ValueError(
                    "Graph channels must encode node identity/labels/types and weighted endpoints"
                )
        return self


class NetworkVisualization(StrictModel):
    type: Literal[ChartType.NETWORK] = ChartType.NETWORK
    title: str
    encoding: NetworkEncoding
    data: NetworkData


class GraphSummary(StrictModel):
    included_studies: NonNegativeInt
    excluded_studies: NonNegativeInt
    missing_sponsor_studies: NonNegativeInt
    no_named_drug_studies: NonNegativeInt
    unnamed_drug_studies: NonNegativeInt
    total_nodes: NonNegativeInt
    total_edges: NonNegativeInt
    displayed_nodes: NonNegativeInt
    displayed_edges: NonNegativeInt
    omitted_nodes: NonNegativeInt
    omitted_edges: NonNegativeInt
    display_truncated: Annotated[bool, Field(strict=True)]
    edge_limit: Literal[200] = GRAPH_EDGE_LIMIT

    @model_validator(mode="after")
    def display_totals(self) -> Self:
        if (
            self.displayed_nodes + self.omitted_nodes != self.total_nodes
            or self.displayed_edges + self.omitted_edges != self.total_edges
            or self.display_truncated != bool(self.omitted_nodes or self.omitted_edges)
            or self.displayed_edges != min(self.edge_limit, self.total_edges)
        ):
            raise ValueError("Graph display and omission totals must be consistent")
        if (
            self.total_nodes > 2 * self.total_edges
            or self.displayed_nodes > 2 * self.displayed_edges
            or (self.total_edges == 0 and self.total_nodes != 0)
        ):
            raise ValueError("Node totals must describe connected bipartite edges")
        if (
            not max(self.missing_sponsor_studies, self.no_named_drug_studies)
            <= self.excluded_studies
            <= (self.missing_sponsor_studies + self.no_named_drug_studies)
        ):
            raise ValueError(
                "Exclusion diagnostics must account for excluded studies, allowing overlap"
            )
        return self
