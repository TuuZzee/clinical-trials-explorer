"""One constrained interpretation call; deterministic validation and override handling."""

import asyncio
import re
from time import perf_counter

from openai import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from pydantic import ValidationError

from app.config import Settings
from app.debug import debug
from app.errors import ServiceError
from app.planner_prompt import SYSTEM_PROMPT as SYSTEM_PROMPT
from app.schemas import (
    PlanningResult,
    PlanningStatus,
    QueryPlan,
    StudyFilters,
    TrialStatus,
    VisualizationRequest,
)
from app.semantics import explicit_year_bounds, validate_plan_constraints, validate_question

# A conservative defense against common analytical requests being recast as counts.
# The model also classifies unsupported requests; this is not a general NLP classifier.
_UNSUPPORTED_ANALYSIS = re.compile(
    r"\b(efficacy|effectiveness|survival\s+(?:improvement|rate|benefit|curves?)|"
    r"improv\w*\s+survival|hazard\s+ratios?|predict\w*|forecast\w*|"
    r"works?\s+best|most\s+effective|response\s+rates?)\b",
    re.IGNORECASE,
)
_RELATIVE_DATES = re.compile(
    r"\b(last|past|next|this)\s+(?:\d+\s+)?(?:years?|months?|weeks?)\b|"
    r"\b(today|yesterday|tomorrow|recently)\b",
    re.IGNORECASE,
)


def _invalid_response() -> ServiceError:
    return ServiceError(
        "planner_invalid_response", "Planner returned an invalid structured response."
    )


def _validation_error(exc: ValidationError) -> ServiceError:
    if any(error["type"] == "unsupported_plan" for error in exc.errors()):
        return ServiceError(
            "unsupported_query", "Unsupported intent, grouping, or chart combination.", 422
        )
    return _invalid_response()


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def merge_request(plan: QueryPlan, request: VisualizationRequest) -> QueryPlan:
    """Explicit non-null filters win. Revalidate, never use an unchecked model_copy."""
    explicit = request.model_dump(include=set(StudyFilters.model_fields), exclude_none=True)
    if plan.comparison and plan.comparison.field.value in explicit:
        raise ServiceError(
            "conflicting_filters",
            "A single explicit filter cannot replace a two-cohort comparison axis.",
            422,
        )
    try:
        filters = StudyFilters.model_validate({**plan.filters.model_dump(), **explicit})
    except ValidationError:
        raise ServiceError("conflicting_filters", "Merged start/end years conflict.", 422) from None
    debug(
        "planner.overrides",
        fields=",".join(sorted(explicit)),
        chart_preference=request.preferred_chart is not None,
    )
    data = plan.model_dump(mode="json")
    data["filters"] = filters.model_dump(mode="json")
    if request.preferred_chart is not None:
        data["chart_type"] = request.preferred_chart.value
    try:
        # Rehydrate enum strings and rerun all constraints, including strict integer years.
        merged = QueryPlan.model_validate(data)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    _validate_grounding(merged, request, set(explicit))
    validate_plan_constraints(merged, request, set(explicit))
    return merged


def _validate_grounding(
    plan: QueryPlan, request: VisualizationRequest, explicit_fields: set[str]
) -> None:
    question = _normalized(request.query)
    for field in ("drug_name", "condition", "sponsor", "country"):
        value = getattr(plan.filters, field)
        if (
            value is not None
            and field not in explicit_fields
            and _normalized(value) not in question
        ):
            raise ServiceError(
                "ambiguous_query",
                "Extracted search phrases must appear literally in the question.",
                422,
            )
    if plan.filters.trial_status is not None and "trial_status" not in explicit_fields:
        status = plan.filters.trial_status
        phrase = status.value.casefold().replace("_", " ")
        status_question = question.replace("_", " ").replace("-", " ")
        unsupported_recruiting = status == TrialStatus.RECRUITING and any(
            term in status_question for term in ("not recruiting", "not yet recruiting")
        )
        if not re.search(rf"\b{re.escape(phrase)}\b", status_question) or unsupported_recruiting:
            raise ServiceError(
                "ambiguous_query", "Use an explicit registry status in the question.", 422
            )
    for field in ("start_year", "end_year"):
        value = getattr(plan.filters, field)
        if value is not None and field not in explicit_fields:
            if (
                not re.search(rf"(?<!\d){value}(?!\d)", question)
                and explicit_year_bounds(question).get(field) != value
            ):
                raise ServiceError(
                    "ambiguous_query", "Use explicit absolute years in the question.", 422
                )
    if plan.comparison:
        if any(_normalized(value) not in question for value in plan.comparison.values):
            raise ServiceError("ambiguous_query", "Name both comparison members explicitly.", 422)


class OpenAIPlanner:
    """Injected client is caller-owned; otherwise a short-lived SDK client is closed per call."""

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.settings = settings
        self.client = client

    async def plan(self, request: VisualizationRequest) -> QueryPlan:
        started = perf_counter()
        debug("planner.start", model=self.settings.openai_model)
        try:
            plan = await self._execute(request)
        except ServiceError as exc:
            debug(
                "planner.failed",
                category=exc.code,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            raise
        except asyncio.CancelledError:
            debug("planner.cancelled", duration_ms=round((perf_counter() - started) * 1000, 2))
            raise
        debug(
            "planner.complete",
            intent=plan.intent.value,
            grouping=plan.group_by.value,
            chart=plan.chart_type.value,
            comparison=plan.comparison is not None,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return plan

    async def _execute(self, request: VisualizationRequest) -> QueryPlan:
        validate_question(request.query)
        if _UNSUPPORTED_ANALYSIS.search(request.query):
            raise ServiceError(
                "unsupported_query",
                "Only registry study counts are supported, not outcome inference or predictions.",
                422,
            )
        if _RELATIVE_DATES.search(request.query):
            raise ServiceError("ambiguous_query", "Specify absolute start/end years.", 422)
        key = self.settings.openai_api_key
        if key is None or not key.get_secret_value().strip():
            raise ServiceError(
                "planner_not_configured", "OPENAI_API_KEY is required for planning.", 503
            )
        try:
            async with asyncio.timeout(self.settings.openai_timeout_seconds):
                if self.client is not None:
                    return await self._plan(self.client, request)
                async with AsyncOpenAI(
                    api_key=key.get_secret_value(),
                    base_url="https://api.openai.com/v1",
                    timeout=self.settings.openai_timeout_seconds,
                    max_retries=0,
                ) as client:
                    return await self._plan(client, request)
        except (TimeoutError, APITimeoutError):
            raise ServiceError("planner_timeout", "OpenAI planning timed out.", 504) from None
        except APIResponseValidationError:
            raise _invalid_response() from None
        except APIConnectionError:
            raise ServiceError(
                "planner_unavailable", "OpenAI planning is unavailable.", 503
            ) from None
        except APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise ServiceError(
                    "planner_unavailable", "OpenAI planning is unavailable.", 503
                ) from None
            raise ServiceError(
                "planner_request_failed",
                "OpenAI rejected the planner request; check model access and configuration.",
            ) from None
        except APIError:
            raise ServiceError(
                "planner_unavailable", "OpenAI planning is unavailable.", 503
            ) from None
        except ValidationError as exc:
            raise _validation_error(exc) from None
        except ValueError:
            raise _invalid_response() from None

    async def _plan(self, client: AsyncOpenAI, request: VisualizationRequest) -> QueryPlan:
        try:
            response = await client.with_options(max_retries=0).responses.parse(
                model=self.settings.openai_model,
                instructions=SYSTEM_PROMPT,
                input=request.query,
                text_format=PlanningResult,
                timeout=self.settings.openai_timeout_seconds,
                max_output_tokens=self.settings.openai_max_output_tokens,
                store=False,
            )
        except (AttributeError, TypeError):
            # The SDK parses a loosely constructed provider envelope before returning it.
            # Malformed envelopes can fail there before Pydantic sees the plan JSON.
            raise _invalid_response() from None
        # Refusals are outside the supplied JSON schema. Never surface their raw text.
        for item in response.output:
            if item.type == "message" and any(
                content.type == "refusal" for content in item.content
            ):
                raise ServiceError(
                    "planner_refused", "The model declined to plan this question.", 422
                )
        if response.status != "completed":
            raise _invalid_response()
        parsed = response.output_parsed
        if not isinstance(parsed, PlanningResult):
            raise _invalid_response()
        result = PlanningResult.model_validate_json(parsed.model_dump_json())
        debug("planner.decision", decision=result.status.value)
        if result.status == PlanningStatus.UNSUPPORTED:
            raise ServiceError(
                "unsupported_query",
                "Question cannot be represented by registry "
                "study counts and supported filters/groupings.",
                422,
            )
        if result.status == PlanningStatus.NEEDS_CLARIFICATION:
            raise ServiceError(
                "ambiguous_query",
                "Specify the grouping, named cohorts, and absolute years where applicable.",
                422,
            )
        assert result.plan is not None  # Envelope validation guarantees this.
        return merge_request(result.plan, request)
