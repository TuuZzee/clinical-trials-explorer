"""Opt-in endpoint checks with independent registry retrievals; no default file writes."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.api_examples import REQUEST_EXAMPLES
from app.clinical_trials import ClinicalTrialsClient
from app.config import Settings
from app.errors import ServiceError
from app.models import QueryPlan, VisualizationRequest, VisualizationResponse
from app.planner import merge_request
from scripts.verification import verify_results

ROOT = Path(__file__).resolve().parents[2]
FEATURED = {
    "phase_distribution": ("phase_distribution", "distribution", "phase", "bar_chart"),
    "annual_trends": ("annual_trends", "time_trend", "start_year", "time_series"),
    "comparison_overlap": ("two_drug_comparison", "comparison", "phase", "grouped_bar_chart"),
    "enrollment_recruiting": (
        "enrollment_histogram",
        "distribution",
        "enrollment_count",
        "histogram",
    ),
    "network_recruiting": (
        "sponsor_drug_network",
        "relationship",
        "sponsor_drug",
        "network_graph",
    ),
}


def example_case(name: str) -> tuple[VisualizationRequest, QueryPlan]:
    example, intent, grouping, chart = FEATURED[name]
    comparison = intent == "comparison"
    data = {**REQUEST_EXAMPLES[example]["value"], "include_citations": False}
    if name in ("enrollment_recruiting", "network_recruiting"):
        data["trial_status"] = "RECRUITING"
    request = VisualizationRequest.model_validate(data)
    plan = QueryPlan.model_validate(
        {
            "intent": intent,
            "measure": "study_count",
            "filters": (
                {"condition": "lung cancer"}
                if comparison
                else {"condition": "breast cancer", "drug_name": "pembrolizumab"}
            ),
            "group_by": grouping,
            "chart_type": chart,
            "comparison": (
                {"field": "drug_name", "values": ["nivolumab", "ipilimumab"]}
                if comparison
                else None
            ),
        }
    )
    return request, merge_request(plan, request)


def verify_interpretation(output: VisualizationResponse, plan: QueryPlan) -> None:
    expected_cohorts = (
        list(zip(plan.comparison.values, plan.cohort_filters(), strict=True))
        if plan.comparison
        else []
    )
    if (
        output.visualization.type != plan.chart_type
        or output.meta.grouping != plan.group_by
        or output.meta.filters != plan.filters
        or output.meta.comparison_field != (plan.comparison.field if plan.comparison else None)
        or [(cohort.label, cohort.filters) for cohort in output.meta.cohorts] != expected_cohorts
    ):
        raise ValueError("Question interpretation differs from the expected plan")


async def check_case(
    name: str,
    base_url: str,
    http: httpx.AsyncClient,
    adapter: ClinicalTrialsClient,
    request: VisualizationRequest,
    plan: QueryPlan,
    output_dir: Path | None = None,
) -> bool:
    try:
        response = await http.post(
            f"{base_url.rstrip('/')}/api/v1/visualize", json=request.model_dump(mode="json")
        )
        if response.status_code != 200:
            print(f"{name}: HTTP {response.status_code}; no output saved.")
            return False
        output = VisualizationResponse.model_validate_json(response.content)
        verify_interpretation(output, plan)
        filters = [cohort.filters for cohort in output.meta.cohorts] or [output.meta.filters]
        results = [await adapter.search(cohort_filters) for cohort_filters in filters]
        details = verify_results(output, results)
        if name == "comparison_overlap" and (
            not all(result.studies for result in results) or not output.meta.overlap_studies
        ):
            raise ValueError("This example must demonstrate nonempty overlapping cohorts")
        if name == "enrollment_recruiting" and not output.meta.histogram.estimated_studies:
            raise ValueError("This example must demonstrate estimated enrollment")
        verification = {
            "case": name,
            "checked_at": datetime.now(UTC).isoformat(),
            "endpoint_status": 200,
            "independent_retrieval_verified": True,
            "retrieved_studies": output.meta.retrieved_studies,
            "total_matches": output.meta.total_matches,
            "complete_results": output.meta.complete_results,
            **details,
        }
        if output_dir is not None:
            for folder, payload in (
                ("requests", request.model_dump(mode="json")),
                ("outputs", response.json()),
            ):
                target = output_dir / folder / f"{name}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload, indent=2) + "\n")
            (output_dir / "outputs" / f"{name}.verification.json").write_text(
                json.dumps(verification, indent=2) + "\n"
            )
        print(json.dumps(verification), flush=True)
        return True
    except (httpx.HTTPError, ServiceError, ValueError, OSError):
        # Do not print response bodies, settings, SDK errors, or exception diagnostics.
        print(f"{name}: verification failed; check service, filters, and registry stability.")
        return False


async def main(base_url: str, case: str, output_dir: Path | None) -> int:
    try:
        settings = Settings()
    except (ValueError, OSError):
        print("Configuration failed validation; inspect local environment settings.")
        return 1
    names = FEATURED if case in ("featured", "all") else (case,)
    async with httpx.AsyncClient(
        timeout=max(180, settings.request_timeout_seconds + 10), follow_redirects=False
    ) as http:
        adapter = ClinicalTrialsClient(http, settings)
        for name in names:
            request, plan = example_case(name)
            if not await check_case(name, base_url, http, adapter, request, plan, output_dir):
                return 1
    return 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow model-backed endpoint/API calls")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--case", choices=["featured", "all", *FEATURED], default="featured")
    parser.add_argument(
        "--output-dir", type=Path, help="Save newly verified runs to this directory"
    )
    args = parser.parse_args(argv)
    if not args.live:
        if args.output_dir:
            parser.error("--output-dir requires --live")
        parser.print_help()
        return 0
    output_dir = args.output_dir.resolve() if args.output_dir else None
    if output_dir and output_dir.is_relative_to(ROOT):
        parser.error("Choose an output directory outside the repository")
    return asyncio.run(main(args.base_url, args.case, output_dir))


if __name__ == "__main__":
    raise SystemExit(cli())
