"""Opt-in planner-only evaluation; no registry retrievals or file writes."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.config import Settings
from app.debug import logger
from app.errors import ServiceError
from app.models import QueryPlan, VisualizationRequest
from app.planner import OpenAIPlanner

CASES_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/planner_reliability.json"


class DecisionCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.decision = None

    def emit(self, record):
        if getattr(record, "event", None) == "planner.decision":
            self.decision = record.details["decision"]


async def evaluate(cases: list[dict]) -> int:
    try:
        settings = Settings()
    except (ValueError, OSError):
        print("Invalid planner configuration.")
        return 1
    capture = DecisionCapture()
    previous = logger.level, logger.propagate, logger.disabled
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.disabled = False
    logger.addHandler(capture)
    failures = 0
    try:
        for case in cases:
            capture.decision = None
            signature = None
            try:
                plan = await OpenAIPlanner(settings).plan(VisualizationRequest(query=case["query"]))
                outcome = "supported"
                matches = case["expected"] == "supported" and plan == QueryPlan.model_validate(
                    case["plan"]
                )
                signature = {
                    "intent": plan.intent.value,
                    "grouping": plan.group_by.value,
                    "chart": plan.chart_type.value,
                    "filter_fields": sorted(plan.filters.model_dump(exclude_none=True)),
                }
            except ServiceError as exc:
                outcome = exc.code
                matches = outcome == case["expected"]
            failures += int(not matches)
            print(
                json.dumps(
                    {
                        "id": case["id"],
                        "model": settings.openai_model,
                        "model_decision": capture.decision,
                        "plan_signature": signature,
                        "outcome": outcome,
                        "matches_expected": matches,
                    }
                ),
                flush=True,
            )
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous[0])
        logger.propagate, logger.disabled = previous[1:]
    return int(bool(failures))


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Allow OpenAI calls using local settings"
    )
    parser.add_argument("--case", action="append", help="Case ID; repeat to select several")
    args = parser.parse_args(argv)
    if not args.live:
        parser.print_help()
        return 0
    cases = json.loads(CASES_PATH.read_text())
    if args.case:
        unknown = set(args.case) - {case["id"] for case in cases}
        if unknown:
            parser.error("Unknown evaluation case ID")
        cases = [case for case in cases if case["id"] in args.case]
    return asyncio.run(evaluate(cases))


if __name__ == "__main__":
    raise SystemExit(cli())
