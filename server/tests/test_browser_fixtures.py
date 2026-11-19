"""Validate synthetic browser responses against the backend's actual contracts."""

import json
from pathlib import Path

import pytest

from app.models import CitedVisualizationResponse, VisualizationResponse

FIXTURES = Path(__file__).resolve().parents[2] / "client/tests/fixtures"


@pytest.mark.parametrize(
    ("name", "chart_type"),
    [
        ("phase_distribution", "bar_chart"),
        ("annual_trends", "time_series"),
        ("comparison_overlap", "grouped_bar_chart"),
        ("enrollment_recruiting", "histogram"),
        ("network_recruiting", "network_graph"),
    ],
)
def test_synthetic_browser_response_contract(name, chart_type):
    fixture = (FIXTURES / "responses" / f"{name}.json").read_text()
    response = VisualizationResponse.model_validate_json(fixture)
    assert response.visualization.type == chart_type


@pytest.mark.parametrize("name", ["phase", "missing", "comparison", "network"])
def test_synthetic_browser_citation_contract(name):
    fixtures = json.loads((FIXTURES / "citations.json").read_text())
    response = CitedVisualizationResponse.model_validate(fixtures[name])
    assert response.citations.reference_count > 0
    if name == "phase":
        ordinary = VisualizationResponse.model_validate_json(
            (FIXTURES / "responses/phase_distribution.json").read_text()
        )
        assert response.model_dump(exclude={"citations"}) == ordinary.model_dump()
