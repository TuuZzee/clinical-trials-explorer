"""Small omission guards for explicit cues, not a general natural-language parser.

The model still interprets free text. These checks reject demonstrated classes of
unsupported clauses and dropped, unambiguously signaled filter roles/bounds.
"""

import re

from app.errors import ServiceError
from app.schemas import QueryIntent, QueryPlan, TrialStatus, VisualizationRequest

# These requests cannot be represented by any current plan. Reject rather than
# accepting a plausible study-count plan that silently omits the unsupported clause.
_UNREPRESENTABLE = re.compile(
    r"\bphase\s*(?:[1-4]|i{1,3}|iv)\b|"
    r"\b(?:top|first|highest|lowest)[- ]+(?:\d+|n|one|two|three|four|five|ten)\b|"
    r"\b(?:daily|weekly|monthly|quarterly)\b|"
    r"\b(?:by|per|each)\s+(?:day|week|month|quarter)\b|"
    r"\b\d{4}-\d{2}(?:-\d{2})?\b|"
    r"\b(?:posting|completion|posted)\s+dates?\b|"
    r"\b(?:posted|posting|completion|completed)\s+(?:in|since|after|before|by)\b|"
    r"\b(?:stock prices?|market share|revenue|commercial partnerships?|patents?|"
    r"cost.effectiveness|median survival|median overall survival)\b",
    re.IGNORECASE,
)

# Deliberately check only explicit role cues. Do not extract values or guess entities.
_ROLE_CUES = {
    "drug_name": r"\b(?:involving|drug\s*:)\s*",
    "condition": r"\b(?:for\s+(?:the\s+)?condition|condition\s*:)\s*",
    "country": r"\b(?:located in|conducted in|country\s*:)\s*",
    "sponsor": r"\b(?:sponsored by|sponsor\s*:)\s*",
}


def validate_question(query: str) -> None:
    if _UNREPRESENTABLE.search(query):
        raise ServiceError(
            "unsupported_query",
            "A requested filter, ranking, time granularity, or measure is unsupported.",
            422,
        )
    if re.search(r"\b(?:networks?|graphs?|relationships?)\b", query, re.IGNORECASE) and re.search(
        r"\b(?:devices?|sponsor[- ](?:to[- ])?sponsor|drug[- ](?:to[- ])?condition)\b",
        query,
        re.IGNORECASE,
    ):
        raise ServiceError(
            "unsupported_query", "Only lead sponsor–drug networks are supported.", 422
        )
    if re.search(r"\bongoing\b", query, re.IGNORECASE):
        raise ServiceError("ambiguous_query", "Specify an explicit registry study status.", 422)


def validate_plan_constraints(
    plan: QueryPlan, request: VisualizationRequest, explicit: set[str]
) -> None:
    validate_question(request.query)
    question = " ".join(request.query.casefold().split())
    if plan.intent == QueryIntent.RELATIONSHIP and not all(
        re.search(pattern, question) for pattern in (r"\bsponsors?\b", r"\bdrugs?\b")
    ):
        raise ServiceError(
            "ambiguous_query", "Specify the lead sponsor-to-drug relationship for a network.", 422
        )
    for field, cue in _ROLE_CUES.items():
        if field in explicit or not re.search(cue, question):
            continue
        if plan.comparison and plan.comparison.field.value == field:
            continue
        value = getattr(plan.filters, field)
        if value is None or not re.search(cue + re.escape(value.casefold()) + r"\b", question):
            raise ServiceError(
                "ambiguous_query",
                "An explicitly requested search constraint was omitted or mismatched.",
                422,
            )
    _validate_described_studies(plan, question, explicit)
    if re.search(r"\b(?:compare|versus|vs)\b", question) and plan.intent != QueryIntent.COMPARISON:
        raise ServiceError(
            "ambiguous_query", "Name two cohorts and a supported comparison grouping.", 422
        )
    if "trial_status" not in explicit:
        text = question.replace("_", " ").replace("-", " ")
        # Longest non-overlapping phrases avoid reading RECRUITING within NOT_YET_RECRUITING.
        phrases = sorted(
            ((s.value.lower().replace("_", " "), s) for s in TrialStatus),
            key=lambda pair: -len(pair[0]),
        )
        mentioned = []
        for phrase, status in phrases:
            pattern = rf"\b{re.escape(phrase)}\b"
            if re.search(pattern, text):
                mentioned.append(status)
                text = re.sub(pattern, " ", text)
        if len(mentioned) > 1:
            raise ServiceError(
                "unsupported_query", "Multiple study statuses are not supported.", 422
            )
        if mentioned and plan.filters.trial_status != mentioned[0]:
            raise ServiceError(
                "ambiguous_query", "A requested study status was omitted or changed.", 422
            )
    bounds = explicit_year_bounds(question)
    for field, value in bounds.items():
        if field not in explicit and getattr(plan.filters, field) != value:
            raise ServiceError(
                "ambiguous_query", "A requested study-start year bound was omitted or changed.", 422
            )


def explicit_year_bounds(question: str) -> dict[str, int]:
    bounds = {}
    for match in re.finditer(r"\b(?:since|from|starting in|on or after)\s+(\d{4})\b", question):
        bounds["start_year"] = int(match[1])
    for match in re.finditer(r"\b(?:through|until|to|on or before)\s+(\d{4})\b", question):
        bounds["end_year"] = int(match[1])
    for match in re.finditer(r"\b(?<!or )after\s+(\d{4})\b", question):
        bounds["start_year"] = int(match[1]) + 1
    for match in re.finditer(r"\b(?<!or )before\s+(\d{4})\b", question):
        bounds["end_year"] = int(match[1]) - 1
    match = re.search(
        r"\bbetween\s+(\d{4})\s+and\s+(\d{4})\b|\b(\d{4})\s*[-–]\s*(\d{4})\b", question
    )
    if match:
        start, end = (match[1], match[2]) if match[1] else (match[3], match[4])
        bounds.update(start_year=int(start), end_year=int(end))
    match = re.search(r"\bin\s+(\d{4})\b", question)
    if match and not re.search(r"\bstarting in\s+\d{4}", question):
        bounds.update(start_year=int(match[1]), end_year=int(match[1]))
    return bounds


def _validate_described_studies(plan: QueryPlan, question: str, explicit: set[str]) -> None:
    # Audit literal descriptors in common "of X trials" / "how many X trials"
    # clauses without deciding whether X is a drug, condition, sponsor, or country.
    # Structured condition/intervention overrides can replace such an ambiguous descriptor.
    if explicit & {"condition", "drug_name"}:
        return
    phrases = [
        value.casefold()
        for value in (
            plan.filters.condition,
            plan.filters.drug_name,
            plan.filters.sponsor,
            plan.filters.country,
        )
        if value
    ]
    if plan.comparison:
        phrases.extend(value.casefold() for value in plan.comparison.values)
    if plan.comparison:
        first, second = (" ".join(value.casefold().split()) for value in plan.comparison.values)
        # Remove a connector only between the two grounded members. In particular,
        # normalize vs. before descriptor matching treats its period as punctuation.
        for left, right in ((first, second), (second, first)):
            question = re.sub(
                rf"(?<!\w)({re.escape(left)})\s+(?:versus|vs\.?)\s+(?={re.escape(right)}(?!\w))",
                r"\1 ",
                question,
            )
    for match in re.finditer(
        r"(?=\b(?:of|for|how many)\s+((?:\bvs\.(?=\s)|[^.,;:?])+?)\s+(?:trials|studies)\b)",
        question,
    ):
        remaining = match[1]
        if re.search(r"\b(?:of|for|by|involving|from|in|since|until|through)\b", remaining):
            continue  # A surrounding chart description, not a single trial descriptor.
        for phrase in sorted(phrases, key=len, reverse=True):
            remaining = re.sub(rf"\b{re.escape(phrase)}\b", " ", remaining)
        for status in sorted(TrialStatus, key=lambda status: -len(status.value)):
            pattern = re.escape(status.value.lower()).replace("_", "[-_ ]+")
            remaining = re.sub(rf"\b{pattern}\b", " ", remaining)
        remaining = re.sub(
            r"\b(?:all|clinical|registered|research|interventional|observational|unique|distinct|and|or)\b",
            " ",
            remaining,
        )
        if re.search(r"[^\W\d_]", remaining):
            raise ServiceError(
                "ambiguous_query",
                "A requested trial descriptor was not represented by the planned search filters.",
                422,
            )
