"""Deterministic registry query compiler and bounded paginated client."""

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.debug import debug
from app.errors import ServiceError
from app.models.enums import Grouping
from app.normalization import (
    Intervention as Intervention,
)
from app.normalization import (
    NormalizedStudy as NormalizedStudy,
)
from app.normalization import (
    normalize_study as normalize_study,
)
from app.provenance import CapturedStudy, EvidenceBudget, capture_study, inconsistent
from app.schemas import StudyFilters

BASE_URL = "https://clinicaltrials.gov/api/v2"


def compile_filters(filters: StudyFilters) -> dict[str, str]:
    params = {"format": "json", "countTotal": "true"}
    for field, parameter in (
        ("drug_name", "query.intr"),
        ("condition", "query.cond"),
        ("sponsor", "query.spons"),
    ):
        if value := getattr(filters, field):
            params[parameter] = f'"{value}"'
    if filters.trial_status:
        params["filter.overallStatus"] = filters.trial_status.value
    advanced = []
    if filters.country:
        advanced.append(f'AREA[LocationCountry]"{filters.country}"')
    if filters.start_year is not None or filters.end_year is not None:
        start = f"{filters.start_year}-01-01" if filters.start_year else "MIN"
        end = f"{filters.end_year}-12-31" if filters.end_year else "MAX"
        advanced.append(f"AREA[StartDate]RANGE[{start},{end}]")
    if advanced:
        params["filter.advanced"] = " AND ".join(advanced)
    return params


class StudyPage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    studies: list[dict[str, Any]]
    nextPageToken: str | None = Field(default=None, min_length=1)
    totalCount: int | None = Field(default=None, ge=0)


class RetrievalResult(BaseModel):
    evidence: dict[str, CapturedStudy] | None = Field(default=None, exclude=True)
    studies: list[NormalizedStudy]
    total_matches: int | None
    retrieved_records: int
    pages_fetched: int
    complete_results: bool = True
    warnings: list[str] = Field(default_factory=list)


class ClinicalTrialsClient:
    """Caller owns the injected AsyncClient lifecycle. Never returns partial totals."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.client = client
        self.settings = settings
        self.sleep = sleep

    async def _page(self, params: dict[str, str | int], page_number: int = 1) -> StudyPage:
        for attempt in range(self.settings.ctgov_max_retries + 1):
            response = None
            started = perf_counter()
            debug(
                "ctgov.request",
                method="GET",
                path=f"{BASE_URL}/studies",
                page=page_number,
                attempt=attempt + 1,
                page_size=params.get("pageSize"),
                token_present=bool(params.get("pageToken")),
                params={
                    key: value
                    for key, value in params.items()
                    if key
                    in {
                        "format",
                        "countTotal",
                        "query.intr",
                        "query.cond",
                        "query.spons",
                        "filter.overallStatus",
                        "filter.advanced",
                        "pageSize",
                    }
                },
            )
            try:
                response = await self.client.get(
                    f"{BASE_URL}/studies",
                    params=params,
                    timeout=self.settings.ctgov_timeout_seconds,
                )
            except asyncio.CancelledError:
                debug(
                    "ctgov.cancelled",
                    page=page_number,
                    attempt=attempt + 1,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
                raise
            except httpx.TransportError:
                debug(
                    "ctgov.response",
                    page=page_number,
                    attempt=attempt + 1,
                    status=None,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    category="transport_error",
                )
                if attempt == self.settings.ctgov_max_retries:
                    debug("ctgov.failed", category="upstream_unavailable")
                    raise ServiceError(
                        "upstream_unavailable", "ClinicalTrials.gov could not be reached.", 503
                    ) from None
            else:
                duration_ms = round((perf_counter() - started) * 1000, 2)
                if response.status_code != 200:
                    debug(
                        "ctgov.response",
                        page=page_number,
                        attempt=attempt + 1,
                        status=response.status_code,
                        duration_ms=duration_ms,
                    )
                if response.status_code == 200:
                    try:
                        page = StudyPage.model_validate(response.json())
                        debug(
                            "ctgov.response",
                            page=page_number,
                            attempt=attempt + 1,
                            status=200,
                            duration_ms=duration_ms,
                            studies=len(page.studies),
                            total=page.totalCount,
                            next_page=bool(page.nextPageToken),
                        )
                        return page
                    except (ValueError, ValidationError):
                        debug(
                            "ctgov.response",
                            page=page_number,
                            attempt=attempt + 1,
                            status=200,
                            duration_ms=duration_ms,
                            category="upstream_invalid_response",
                        )
                        debug("ctgov.failed", category="upstream_invalid_response")
                        raise ServiceError(
                            "upstream_invalid_response",
                            "ClinicalTrials.gov returned malformed study data.",
                        ) from None
                retryable = response.status_code == 429 or 500 <= response.status_code <= 599
                if not retryable:
                    debug("ctgov.failed", category="upstream_rejected_request")
                    raise ServiceError(
                        "upstream_rejected_request",
                        "ClinicalTrials.gov rejected the compiled request.",
                    )
                if attempt == self.settings.ctgov_max_retries:
                    debug("ctgov.failed", category="upstream_unavailable")
                    raise ServiceError(
                        "upstream_unavailable", "ClinicalTrials.gov retry budget exhausted.", 503
                    )
            delay = self.settings.ctgov_backoff_seconds * 2**attempt
            if response is not None:
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isascii() and retry_after.isdigit():
                    delay = max(delay, int(retry_after))
            delay = min(delay, self.settings.ctgov_max_retry_delay_seconds)
            debug(
                "ctgov.retry",
                page=page_number,
                attempt=attempt + 1,
                next_attempt=attempt + 2,
                backoff_seconds=delay,
            )
            await self.sleep(delay)
        raise AssertionError("unreachable")

    async def search(
        self,
        filters: StudyFilters,
        *,
        evidence_grouping: Grouping | None = None,
        evidence_budget: EvidenceBudget | None = None,
    ) -> RetrievalResult:
        base_params = compile_filters(filters)
        studies: dict[str, NormalizedStudy] = {}
        evidence = {} if evidence_grouping is not None else None
        if evidence_grouping is not None and evidence_budget is None:
            raise ValueError("Evidence capture requires a shared bounded budget")
        seen_tokens: set[str] = set()
        token = None
        total = None
        records = 0
        for page_number in range(1, self.settings.ctgov_max_pages + 1):
            remaining = self.settings.ctgov_max_studies - records
            params: dict[str, str | int] = {
                **base_params,
                "pageSize": min(self.settings.ctgov_page_size, remaining),
            }
            if token:
                params["pageToken"] = token
            page = await self._page(params, page_number)
            if page.totalCount is not None:
                if total is not None and total != page.totalCount:
                    debug("ctgov.failed", category="incomplete_retrieval")
                    raise ServiceError(
                        "incomplete_retrieval", "Registry total changed during paging."
                    )
                total = page.totalCount
                if total > self.settings.ctgov_max_studies:
                    self._incomplete()
            records += len(page.studies)
            if records > self.settings.ctgov_max_studies:
                self._incomplete()
            for raw in page.studies:
                try:
                    study = normalize_study(raw)
                except ServiceError as exc:
                    debug("ctgov.failed", category=exc.code)
                    raise
                if study.nct_id not in studies:
                    if evidence is not None:
                        try:
                            captured = capture_study(raw, study, evidence_grouping, evidence_budget)
                        except ServiceError:
                            raise
                        except (ValueError, TypeError, RecursionError):
                            raise inconsistent() from None
                        if captured is not None:
                            evidence[study.nct_id] = captured
                    studies[study.nct_id] = study
            token = page.nextPageToken
            if not token:
                if total is not None and len(studies) != total:
                    debug("ctgov.failed", category="incomplete_retrieval")
                    raise ServiceError(
                        "incomplete_retrieval",
                        "Distinct study count does not match the registry total.",
                    )
                warnings = []
                if records != len(studies):
                    warnings.append("Duplicate NCT IDs were removed; first record retained.")
                missing_years = sum(study.start_year is None for study in studies.values())
                if missing_years:
                    warnings.append(f"{missing_years} studies have missing or invalid start years.")
                debug("ctgov.complete", pages=page_number, retrieved=records, distinct=len(studies))
                return RetrievalResult(
                    evidence=evidence,
                    studies=list(studies.values()),
                    total_matches=total,
                    retrieved_records=records,
                    pages_fetched=page_number,
                    warnings=warnings,
                )
            if token in seen_tokens:
                debug("ctgov.failed", category="upstream_invalid_response")
                raise ServiceError(
                    "upstream_invalid_response", "Registry repeated a pagination token."
                )
            seen_tokens.add(token)
            if records >= self.settings.ctgov_max_studies:
                self._incomplete()
        self._incomplete()

    @staticmethod
    def _incomplete() -> None:
        debug("ctgov.failed", category="incomplete_retrieval")
        raise ServiceError(
            "incomplete_retrieval",
            "Retrieval cap exceeded. Narrow filters or increase configured caps.",
            422,
        )
