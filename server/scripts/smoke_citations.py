"""Opt-in phase citation verification against fresh raw study records."""

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
from app.models import CitedVisualizationResponse, VisualizationResponse
from app.models.citations import MissingField, ObjectKeys, SourceValue
from scripts.verification import PHASES, verify_results

ROOT = Path(__file__).resolve().parents[2]


def read_pointer(source, path):
    for token in path.split("/")[1:] if path else []:
        token = token.replace("~1", "/").replace("~0", "~")
        source = source[int(token)] if isinstance(source, list) else source[token]
    return source


def verify_raw_phase_citations(output: CitedVisualizationResponse, records: dict[str, dict]) -> int:
    """Independent raw-field checks: no application normalizer or citation selector."""
    if output.meta.grouping != "phase" or output.meta.cohorts:
        raise ValueError("This focused check supports a single phase distribution")
    expected = {}
    for nct_id, raw in records.items():
        if raw["protocolSection"]["identificationModule"]["nctId"].strip() != nct_id:
            raise ValueError("Raw record identity disagrees")
        design = raw.get("protocolSection", {}).get("designModule", {})
        phases = design.get("phases", []) if isinstance(design, dict) else []
        values = (
            {value.strip() for value in phases if isinstance(value, str) and value.strip()}
            if isinstance(phases, list)
            else set()
        )
        labels = {PHASES.get(value, f"Unrecognized phase: {value}") for value in values} or {
            "Missing phase"
        }
        for label in labels:
            expected.setdefault(label, set()).add(nct_id)
    checked = 0
    for index, datum in enumerate(output.citations.datums):
        label = output.visualization.data[index]["phase"]
        if {study.nct_id for study in datum.studies} != expected.get(label, set()):
            raise ValueError("Raw phase contributors disagree")
        for study in datum.studies:
            raw = records[study.nct_id]
            phase_fields = [
                f for f in study.fields if f.path.startswith("/protocolSection/designModule/phases")
            ]
            if not phase_fields:
                raise ValueError("No phase source evidence")
            if label != "Missing phase" and not any(
                isinstance(f, SourceValue)
                and isinstance(f.value, str)
                and PHASES.get(f.value.strip(), f"Unrecognized phase: {f.value.strip()}") == label
                for f in phase_fields
            ):
                raise ValueError("Quoted fields do not support the reported phase")
            for evidence in study.fields:
                if isinstance(evidence, MissingField):
                    try:
                        read_pointer(raw, evidence.path)
                    except (KeyError, IndexError, TypeError, ValueError):
                        pass
                    else:
                        raise ValueError("Claimed missing field exists")
                    ancestor = read_pointer(raw, evidence.ancestor.path)
                    actual = (
                        sorted(ancestor) if isinstance(evidence.ancestor, ObjectKeys) else ancestor
                    )
                    quoted = (
                        evidence.ancestor.keys
                        if isinstance(evidence.ancestor, ObjectKeys)
                        else evidence.ancestor.value
                    )
                else:
                    actual = read_pointer(raw, evidence.path)
                    quoted = evidence.value
                if json.dumps(actual, sort_keys=True) != json.dumps(quoted, sort_keys=True):
                    raise ValueError("Quoted source differs from raw API response")
                checked += 1
    return checked


async def main(base_url: str, output_dir: Path | None) -> int:
    try:
        settings = Settings()
        request = REQUEST_EXAMPLES["phase_distribution"]["value"]
        async with httpx.AsyncClient(
            timeout=max(180, settings.request_timeout_seconds + 10), follow_redirects=False
        ) as http:
            cited_reply = await http.post(
                f"{base_url.rstrip('/')}/api/v1/visualize",
                json={**request, "include_citations": True},
            )
            cited_reply.raise_for_status()
            output = CitedVisualizationResponse.model_validate_json(cited_reply.content)
            if output.citations.reference_count == 0:
                raise ValueError("This live example must demonstrate positive source evidence")
            plain_reply = await http.post(
                f"{base_url.rstrip('/')}/api/v1/visualize",
                json={**request, "include_citations": False},
            )
            plain_reply.raise_for_status()
            ordinary = VisualizationResponse.model_validate_json(plain_reply.content)
            if "citations" in plain_reply.json() or output.model_dump(
                mode="json", exclude={"citations"}
            ) != ordinary.model_dump(mode="json"):
                raise ValueError("Ordinary and cited visualizations disagree")
            retrieved = await ClinicalTrialsClient(http, settings).search(output.meta.filters)
            verify_results(output, [retrieved])
            records = {}
            for study in retrieved.studies:
                reply = await http.get(
                    f"https://clinicaltrials.gov/api/v2/studies/{study.nct_id}",
                    timeout=settings.ctgov_timeout_seconds,
                )
                reply.raise_for_status()
                records[study.nct_id] = reply.json()
            checked = verify_raw_phase_citations(output, records)
        evidence = {
            "checked_at": datetime.now(UTC).isoformat(),
            "endpoint_status": 200,
            "independent_retrieval_verified": True,
            "raw_source_fields_verified": checked,
            "retrieved_studies": len(records),
            "citation_references": output.citations.reference_count,
            "ordinary_response_unchanged": True,
            "note": "Fresh raw GETs can differ if the live registry changes between requests.",
        }
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            for suffix, body in (
                ("request", {**request, "include_citations": True}),
                ("response", cited_reply.json()),
                ("verification", evidence),
            ):
                (output_dir / f"phase_distribution.{suffix}.json").write_text(
                    json.dumps(body, indent=2) + "\n"
                )
        print(json.dumps(evidence), flush=True)
        return 0
    except (ServiceError, httpx.HTTPError, ValueError, OSError, KeyError, TypeError):
        print("Citation verification failed. Check service, source stability, and limits.")
        return 1


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow two model-backed requests and public registry checks",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if not args.live:
        if args.output_dir:
            parser.error("--output-dir requires --live")
        parser.print_help()
        return 0
    target = args.output_dir.resolve() if args.output_dir else None
    if target and target.is_relative_to(ROOT):
        parser.error("Choose an output directory outside the repository")
    return asyncio.run(main(args.base_url, target))


if __name__ == "__main__":
    raise SystemExit(cli())
