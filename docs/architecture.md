# Clinical Trials Explorer architecture

[Project overview](../README.md#how-it-works).

## Request pipeline

1. FastAPI validates `VisualizationRequest` and loads settings.
2. `OpenAIPlanner` makes one Responses API Structured Outputs call.
3. Pydantic and semantic guards validate the plan; explicit non-null request fields
   override extracted filters and the merged plan is validated again.
4. The registry adapter compiles allowlisted fields into API parameters and retrieves
   each cohort sequentially, stopping on the first failure.
5. Pure Python aggregates records and constructs a typed visualization.
6. Aggregation, validation, and serialization run in a process-local thread. The
   final JSON is validated again before the HTTP response is returned.

FastAPI dependencies own and close their HTTP clients per request/call. They can be
replaced in tests. Reusing pooled clients could reduce setup overhead later; the
current lifecycle keeps ownership and cleanup explicit.

## Constrained planning

The OpenAI SDK uses `AsyncOpenAI.responses.parse(text_format=PlanningResult)`.
The response envelope has `status` (`supported`, `unsupported`, `needs_clarification`)
and nullable `plan`. Only supported results contain a plan.

A `QueryPlan` contains typed `intent`, `measure` (`study_count` only), `filters`,
`group_by`, `chart_type`, and nullable `comparison`. Comparison is exactly two
non-equivalent literal phrases with field `drug_name` or `condition`. Shared filters
cannot restrict the compared axis. Supported combinations are enforced locally.
The plan has no raw API expressions, arbitrary field paths, sorting/limit language,
result values, executable content, or URLs.

Extracted phrases and comparison members must occur literally in the question,
ignoring case and whitespace. Years must occur explicitly, except the deterministic
adjustment for `after YEAR` / `before YEAR`. These exclude the given year;
`since`/`from` and `through`/`until` are inclusive. `in YEAR` uses both bounds.
Status extraction requires an explicit registry status phrase. Structured fields
bypass grounding because they are explicit user input.

Semantic guards also audit common omissions, swapped filter roles, unsupported
filters, date granularity, ranking, and outcome requests. They are bounded rules,
not a second general-purpose NLP parser. Unfamiliar wording can be rejected or a
constraint can escape detection. The model remains responsible for interpretation.

The model call has no automatic SDK retries. There is a network timeout and an outer
planning deadline. Refusals, truncated output, invalid envelopes, and provider failures
are handled separately. Requests use `store=false`; provider retention policies still apply.

## Registry retrieval

The fixed API base is `https://clinicaltrials.gov/api/v2`.
`query.cond`, `query.intr`, and `query.spons` receive quoted literal phrases.
`filter.overallStatus` receives the enum value. `filter.advanced` combines
`AREA[LocationCountry]` and inclusive `AREA[StartDate]RANGE[...]` with AND;
absent year bounds use MIN/MAX. Search behavior is defined by the registry.

Every request asks for JSON and `countTotal=true`. Pagination follows `nextPageToken`
as `pageToken`, retaining the filters even across empty pages. Raw records count
toward caps, so duplicates cannot make paging unbounded. Repeated tokens, changing
totals, remaining cursors at a cap, and final distinct-count mismatches fail closed.
Token exhaustion without a registry total is complete, with `total_matches: null`.
There is no transactional snapshot; some changes between pages may be undetectable.

Transport errors, HTTP 429, and 5xx retry within the configured budget. Numeric
`Retry-After` seconds are honored up to the delay cap; HTTP-date values fall back to
exponential delay. Permanent errors and malformed payloads do not retry.

Normalization retains the fields needed for grouping and NCT IDs for deduplication.
Missing optional values become null/empty collections. Missing or invalid NCT identity
is fatal. Repeated IDs retain the first record. Optional citation requests retain only
selected original fields and their source positions alongside that same first record;
see [source citations](citations.md).

The small `app/schemas.py` and `app/extra_charts.py` modules retain import compatibility;
contracts and builders live in their domain modules. Keeping these re-exports avoids
import churn without adding another runtime layer.

## API contract

### Request

`POST /api/v1/visualize` accepts a JSON object. Only `query` is required.

```json
{
  "query": "Show phases of breast cancer trials involving pembrolizumab",
  "sponsor": "National Cancer Institute",
  "country": "United States",
  "trial_status": "COMPLETED",
  "start_year": 2015,
  "end_year": 2025
}
```

| Field | Meaning |
| --- | --- |
| `query` | Trimmed string, 1–2000 characters |
| `drug_name`, `condition` | Literal intervention/condition search phrases |
| `country` | Trial-location country |
| `sponsor` | Sponsor **or collaborator** search phrase |
| `trial_status` | Registry status enum; values appear in `/docs` |
| `start_year`, `end_year` | Inclusive study-start bounds, integer years 1800–2100 |
| `preferred_chart` | One of the five visualization types |
| `include_citations` | Strict boolean, default false |

Other optional fields default to null. Explicit non-null fields override extracted
filters. Null leaves extracted filters in place. The merged plan is validated again.
A singular filter on the compared drug or condition axis conflicts with a two-cohort
comparison.

Unknown fields are rejected. Search phrases are trimmed to 1–200 characters and
cannot contain quotes, backslashes, brackets/braces, or control characters.
Year bounds must be ordered integers.

### Response

Successful responses contain `visualization` and `meta`. Each visualization has
`type`, `title`, `encoding`, and `data`. Encoding channels name their field, type,
and display title. Render categories in the returned order.

| Type | Data shape |
| --- | --- |
| `bar_chart` | Category and integer `trial_count`; `x`, `y` |
| `time_series` | `start_year`, `trial_count`; temporal `x` uses `YYYY-01-01` year anchors |
| `grouped_bar_chart` | Category, `trial_count`, `cohort`; adds nominal `series` |
| `histogram` | `bin_label`, `bin_lower`, `bin_upper`, `trial_count`; numeric `x`, `x2`, `y` |
| `network_graph` | `{nodes, edges}` with identity/label/type and endpoint/weight channels |

Metadata includes the source, effective filters, grouping, counting unit, distinct
retrieved studies, nullable registry total, completeness, and warnings. Comparisons
add per-cohort metadata and overlap. The top-level population counts the distinct
union. Histograms and networks add their own summaries. Empty matches return empty
data. Upstream failures return errors.

[Data and counting](data-and-counting.md) defines exact row keys, bin boundaries,
node identities, ordering, missing-data rules, and metadata invariants.

See [source citations](citations.md) for the optional evidence bundle. The
[error table](#errors) lists HTTP status codes and error details.

## Configuration

Run the backend from `server/` so settings can load its local `.env`.
Environment variables override values in the file. Invalid
settings do not silently fall back to defaults and are never reflected in HTTP errors.

| Variable | Default | Allowed range / meaning |
| --- | --- | --- |
| `REQUEST_TIMEOUT_SECONDS` | 120 | >0–300 seconds, total execution budget |
| `OPENAI_API_KEY` | unset | Nonblank secret required for planning |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Nonempty model ID without whitespace |
| `OPENAI_TIMEOUT_SECONDS` | 30 | >0–120 seconds, network and total planning budget |
| `OPENAI_MAX_OUTPUT_TOKENS` | 2000 | 256–8000; truncated output is rejected |
| `CTGOV_TIMEOUT_SECONDS` | 20 | >0–120 seconds, per network operation |
| `CTGOV_PAGE_SIZE` | 100 | 1–1000 |
| `CTGOV_MAX_PAGES` | 50 | 1–1000 per cohort |
| `CTGOV_MAX_STUDIES` | 5000 | 1–100000 raw records per cohort |
| `CTGOV_MAX_RETRIES` | 2 | 0–5 retries after the initial attempt, per page |
| `CTGOV_BACKOFF_SECONDS` | 0.5 | 0–5 seconds, exponential base delay |
| `CTGOV_MAX_RETRY_DELAY_SECONDS` | 5 | 0–30 seconds, maximum retry sleep |
| `CITATION_MAX_REFERENCES` | 1000 | 1–100000 datum-to-study contributions |
| `CITATION_MAX_BYTES` | 1000000 | 1–100000000 bytes; capture and bundle JSON bounds |

Comparisons may retrieve twice the per-cohort cap. The API reads the registry for
each query. This avoids maintaining a registry copy and makes latency and availability
dependent on the upstream service.

## Deadlines and cancellation

The total execution budget begins after request/dependency validation. It includes
planning, registry pages and retry sleeps, both cohorts, aggregation, validation,
and serialization. Expiry returns 504 without partial results. Task cancellation
propagates through planner/HTTP/retry awaits; it is not converted into an empty chart.

Cancelling a thread await does not forcibly stop already running Python work. Bounded
aggregation may finish after the response deadline. The budget does not cover slow
body upload, dependency initialization, socket transmission, or guarantee immediate
client-disconnect detection. Parsing and scheduling remain cooperative. This is not
a hard CPU limit or a production concurrency guarantee.

## Development logging

Run from `server/`.

```sh
uv run uvicorn app.main:app --reload --log-level debug
```

The application logger `uvicorn.error.query_viz` inherits the level and handlers of
Uvicorn's error logger. This command enables its DEBUG events without changing root
or third-party logger configuration. Use `--log-level info` (the default) to hide them.

Events show validated planning decisions, pipeline stages, registry GET parameters,
page/attempt numbers, token presence, status, elapsed milliseconds, page populations,
retry sleeps, and final retrieval totals. A generated request-local ID correlates
planning, paging, and aggregation, including work offloaded to a thread. Durations
use a monotonic clock and failures use sanitized categories.

DEBUG logs can contain user-supplied search filters. Strings have control characters
removed, key-shaped strings redacted, and displayed lengths bounded. Raw questions,
headers/credentials, provider bodies, refusal text, and opaque cursor values are not
included. Use DEBUG only where these search terms may be recorded appropriately;
third-party loggers remain under their own configuration. Logging stream failures
are isolated from request processing.

## Errors

All documented errors use `{"error":{"code":"...","message":"..."}}`.
Validation inputs, upstream bodies/headers, raw refusal text, and SDK diagnostics
are excluded. Errors never produce plausible-looking empty chart results.

| Status | Code | Meaning / next step |
| --- | --- | --- |
| 422 | `citation_limit_exceeded` | Narrow filters or disable citations; no partial evidence |
| 500 | `invalid_citations` | Evidence consistency failed; no chart returned |
| 422 | `invalid_request` | Fix request fields using `/docs` |
| 422 | `unsupported_query` | Use a supported registry-count interpretation |
| 422 | `ambiguous_query` | Specify grouping, named cohorts, or absolute years |
| 422 | `conflicting_filters` | Fix conflicting merged bounds or comparison-axis filter |
| 422 | `planner_refused` | Model declined to plan the question |
| 422 | `incomplete_retrieval` | Cap exceeded; narrow filters or review caps |
| 500 | `invalid_visualization` | Final output contract failed; no chart returned |
| 501 | `not_implemented` | Defensive fallback for a future plan without a builder |
| 502 | `incomplete_retrieval` | Inconsistent retrieval or registry total |
| 502 | `upstream_invalid_response` | Malformed payload, invalid identity, or repeated cursor |
| 502 | `upstream_rejected_request` | Registry rejected a compiled request |
| 502 | `planner_request_failed` | Review model access/configuration/schema |
| 502 | `planner_invalid_response` | Invalid or incomplete model output |
| 503 | `invalid_configuration` | Fix invalid/unreadable server settings |
| 503 | `planner_not_configured` | Configure a nonblank API key |
| 503 | `planner_unavailable` | OpenAI transport or service failure |
| 503 | `upstream_unavailable` | Registry retry budget exhausted |
| 504 | `request_timeout` | Narrow filters or review total execution budget |
| 504 | `planner_timeout` | OpenAI network or planning deadline expired |

Malformed extracted year ranges are model-output errors (502). A valid extracted
range made inconsistent by explicit overrides is a filter conflict (422).
