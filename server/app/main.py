import asyncio
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Body, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from app.api_examples import REQUEST_EXAMPLES
from app.charts import build_chart, require_supported_plan, validate_retrieval
from app.citations import build_cited_response, validate_evidence
from app.clinical_trials import ClinicalTrialsClient, RetrievalResult
from app.config import Settings
from app.debug import debug, request_id
from app.errors import ServiceError
from app.models import CitedVisualizationResponse
from app.planner import OpenAIPlanner
from app.provenance import EvidenceBudget, inconsistent
from app.schemas import ErrorResponse, QueryPlan, VisualizationRequest, VisualizationResponse

app = FastAPI(title="Clinical Trials Explorer", version="0.1.0")


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}}
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Avoid reflecting user inputs or credentials in validation responses.
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "Request failed validation; see /docs for the schema.",
            }
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Per-request dependencies keep resource ownership explicit and are overrideable in tests.
def get_settings() -> Settings:
    try:
        return Settings()
    except (ValidationError, ValueError, OSError):
        raise ServiceError(
            "invalid_configuration",
            "Server configuration is invalid; check environment settings.",
            503,
        ) from None


def get_planner(settings: Annotated[Settings, Depends(get_settings)]) -> OpenAIPlanner:
    return OpenAIPlanner(settings)


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield client


def get_clinical_trials(
    settings: Annotated[Settings, Depends(get_settings)],
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> ClinicalTrialsClient:
    return ClinicalTrialsClient(http_client, settings)


@app.post(
    "/api/v1/visualize",
    response_model=VisualizationResponse | CitedVisualizationResponse,
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Invalid, unsupported, ambiguous, or capped query",
        },
        500: {"model": ErrorResponse, "description": "Generated chart failed contract validation"},
        501: {
            "model": ErrorResponse,
            "description": "Valid plan without an implemented chart builder",
        },
        502: {
            "model": ErrorResponse,
            "description": "Invalid/rejected upstream response or incomplete data",
        },
        503: {
            "model": ErrorResponse,
            "description": "Invalid settings, missing credentials, or unavailable upstream",
        },
        504: {
            "model": ErrorResponse,
            "description": "Planner timeout or total request deadline exceeded",
        },
    },
)
async def visualize(
    payload: Annotated[VisualizationRequest, Body(openapi_examples=REQUEST_EXAMPLES)],
    planner: Annotated[OpenAIPlanner, Depends(get_planner)],
    clinical_trials: Annotated[ClinicalTrialsClient, Depends(get_clinical_trials)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    deadline = asyncio.timeout(settings.request_timeout_seconds)
    started = perf_counter()
    token = request_id.set(uuid4().hex[:12])
    debug("pipeline.start")
    try:
        async with deadline:
            plan = await planner.plan(payload)
            require_supported_plan(plan)
            results = []
            evidence_budget = (
                EvidenceBudget(settings.citation_max_bytes) if payload.include_citations else None
            )
            cohorts = plan.cohort_filters()
            debug("pipeline.planned", cohorts=len(cohorts))
            for index, filters in enumerate(cohorts, start=1):
                debug("pipeline.retrieve.start", cohort=index)
                if payload.include_citations:
                    result = await clinical_trials.search(
                        filters, evidence_grouping=plan.group_by, evidence_budget=evidence_budget
                    )
                else:
                    result = await clinical_trials.search(filters)
                validate_retrieval(result)
                results.append(result)
                debug("pipeline.retrieve.complete", cohort=index, studies=len(result.studies))
            # Keep aggregation, validation, and serialization off the event loop.
            debug("pipeline.aggregate.start")
            if payload.include_citations:
                content = await asyncio.to_thread(
                    _render_cited_response,
                    plan,
                    results,
                    settings.citation_max_references,
                    settings.citation_max_bytes,
                )
            else:
                content = await asyncio.to_thread(_render_response, plan, results)
            debug("pipeline.complete", duration_ms=round((perf_counter() - started) * 1000, 2))
            return Response(content=content, media_type="application/json")
    except TimeoutError:
        if not deadline.expired():
            raise
        debug(
            "pipeline.failed",
            category="request_timeout",
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        raise ServiceError(
            "request_timeout",
            "Visualization request exceeded its time budget; narrow the filters.",
            504,
        ) from None
    except ServiceError as exc:
        debug(
            "pipeline.failed",
            category=exc.code,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        raise
    except asyncio.CancelledError:
        debug("pipeline.cancelled", duration_ms=round((perf_counter() - started) * 1000, 2))
        raise
    finally:
        request_id.reset(token)


def _render_response(plan: QueryPlan, results: list[RetrievalResult]) -> str:
    # Builders construct a validated response. Serialize here so the deadline also
    # covers rendering; no unvalidated dict or model-generated values bypass validation.
    started = perf_counter()
    response = build_chart(plan, results)
    if not isinstance(response, VisualizationResponse):
        raise ServiceError(
            "invalid_visualization", "Generated visualization failed validation.", 500
        )
    try:
        content = response.model_dump_json(warnings="error")
        VisualizationResponse.model_validate_json(content)
        debug(
            "pipeline.aggregate.complete",
            chart=response.visualization.type.value,
            distinct=response.meta.retrieved_studies,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return content
    except (ValidationError, PydanticSerializationError):
        raise ServiceError(
            "invalid_visualization", "Generated visualization failed validation.", 500
        ) from None


def _render_cited_response(
    plan: QueryPlan, results: list[RetrievalResult], max_references: int, max_bytes: int
) -> str:
    # Reuse the ordinary chart validation before adding independent top-level evidence.
    ordinary = VisualizationResponse.model_validate_json(_render_response(plan, results))
    cited = build_cited_response(ordinary, plan, results, max_references, max_bytes)
    try:
        content = cited.model_dump_json(warnings="error")
        validated = CitedVisualizationResponse.model_validate_json(content)
        validate_evidence(validated, plan, results, max_references)
        return content
    except (ValueError, TypeError, PydanticSerializationError):
        raise inconsistent() from None
