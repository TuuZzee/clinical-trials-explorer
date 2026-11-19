"""Opt-in public API smoke check. No OpenAI call or credentials required.

Run: python -m scripts.smoke_ctgov
"""

import asyncio
import json
from datetime import UTC, datetime

import httpx

from app.clinical_trials import ClinicalTrialsClient, compile_filters
from app.config import Settings
from app.schemas import StudyFilters


async def main() -> None:
    filters = StudyFilters(
        drug_name="pembrolizumab",
        condition="breast cancer",
        sponsor="National Cancer Institute",
        country="United States",
        trial_status="COMPLETED",
        start_year=2015,
        end_year=2025,
    )
    settings = Settings(_env_file=None, ctgov_page_size=1, ctgov_max_studies=10, ctgov_max_pages=10)
    async with httpx.AsyncClient(follow_redirects=False) as http:
        result = await ClinicalTrialsClient(http, settings).search(filters)
    print(
        json.dumps(
            {
                "checked_at": datetime.now(UTC).isoformat(),
                "parameters": compile_filters(filters),
                "pages_fetched": result.pages_fetched,
                "retrieved_records": result.retrieved_records,
                "unique_studies": len(result.studies),
                "total_matches": result.total_matches,
                "complete_results": result.complete_results,
                "nct_ids": [study.nct_id for study in result.studies],
                "warnings": result.warnings,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
