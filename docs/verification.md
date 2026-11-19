# Verification

[Project overview](../README.md#quality-and-limitations).

## Offline tests

Run the backend suite from the repository root.

```sh
cd server
uv sync --locked --extra dev --python 3.12
uv run --locked pytest -q -W error
uv run --locked ruff check app tests scripts
uv run --locked ruff format --check app tests scripts
```

The backend suite has **593 cases** across 18 modules. Tests mock both upstream
services and use synthetic credentials. The application and mocked transports use
`httpx`. The development dependency `httpx2` supports Starlette's TestClient.

The [client checks](../client/README.md#verify) include **17 Playwright cases** for
the production build, server-side proxy, and all five chart renderers. The browser
suite uses [synthetic fixtures](../client/tests/fixtures/README.md) through a
local HTTP fixture server. The backend suite checks these responses against the
API models. GitHub Actions runs both suites on pull requests targeting main branch.

### Test responsibilities

The suite exercises schema validation, chart builders, original source fields, and
HTTP behavior at their respective boundaries.

| Module under `server/tests/` | Responsibility |
| --- | --- |
| `test_api.py` | Routes, errors, actual SDK/adapter pipeline, cohorts, caps; shared SDK envelope helper |
| `test_api_hardening.py` | Malformed/configuration inputs, final validation, deadlines and cancellation |
| `test_browser_fixtures.py` | Browser visualization and citation fixtures against the backend response models |
| `test_chart_contract.py` | Cartesian row/channel/metadata invariants and strict numeric counts |
| `test_charts.py` | Categorical/year counts, missing/unknown values, overlap, empty cohorts |
| `test_citations.py` | Exact raw evidence, all groupings, missing states, limits, corruption, duplicate policies |
| `test_citations_api.py` | Opt-in bytes, no extra retrievals/model input, errors, deadlines, logging |
| `test_clinical_trials.py` | Query compilation, paging, retry boundaries, completeness, parsing and cancellation |
| `test_debug_logging.py` | Correlation, redaction, logger configuration, evaluator isolation |
| `test_histogram.py` | Every bin boundary, excluded counts/types, duplicate policy and output contract |
| `test_network.py` | Weights, eligibility, stable identities, exclusions, ordering and display cap |
| `test_normalization_sources.py` | Synthetic field paths and distractors, without private parsing helpers |
| `test_planner.py` | Typed plans, overrides, grounding, envelope decisions and supported combinations |
| `test_planner_reliability.py` | Comparison wording/cardinality and known omission regressions |
| `test_planner_transport.py` | Real SDK request/schema, refusals, malformed responses, timeouts and no retries |
| `test_semantic_evaluation.py` | Adversarial omissions/swapped roles, explicit constraints and OpenAPI example requests |
| `test_smoke_examples.py` | Independent verifier branches, opt-in writes, failures and repository output guard |
| `test_verification.py` | Independent scalar/reference comparisons reject structurally valid wrong counts |

## Source and planner checks

The documented [synthetic registry fixture](../server/tests/fixtures/README.md) is checked
directly for scalar and collection fields. Synthetic variants cover missing modules,
multiple categories, estimated/invalid enrollment, and unrelated distractor fields.
Coverage focuses on the fields the application uses.

The [semantic matrix](../server/tests/fixtures/semantic_evaluation.json) and
[planner regression set](../server/tests/fixtures/planner_reliability.json) inject plans
into the real SDK parsing path to check application validation. The test inputs
include plans with omitted or contradictory constraints and responses the
application must reject.

Run selected live checks from `server/` with an OpenAI API key. Calls may incur API charges.

```sh
uv run python -m scripts.evaluate_planner --live \
  --case phase_reported --case network_reported --case comparison_versus
```

Without `--live`, it only shows help. Negative fixture plans are adversarial inputs
for server validation, so live disagreement requires review. The tool emits case IDs,
decisions, outcomes, and plan signatures; it restores logger configuration and does
not rewrite fixtures or recordings. General interpretation remains imperfect.

## Live checks

Start the API with the README instructions and configured credentials. Run the
following commands from `server/` in a separate terminal. These tools
are opt-in; ordinary pytest does not contact either upstream.

```sh
# Help only:
uv run python -m scripts.smoke_examples
# One example case or all five:
uv run python -m scripts.smoke_examples --live --case phase_distribution
uv run python -m scripts.smoke_examples --live --case featured
# Save a fresh verified run separately:
uv run python -m scripts.smoke_examples --live --case comparison_overlap \
  --output-dir "${TMPDIR:-/tmp}/clinical-trials-verification"
# Raw citation checks (two model-backed endpoint requests):
uv run python -m scripts.smoke_citations --live
# Registry connectivity/paging only, without a key or model call:
uv run python -m scripts.smoke_ctgov
```

The endpoint defaults to `http://127.0.0.1:8000`; use `--base-url` to override it.
Each smoke-example case uses one model-backed request and one registry retrieval
per cohort. The tool compares the returned interpretation with an explicit plan
for the OpenAPI example and independently recalculates values from fresh registry
records. Unexpected interpretations, failed or incomplete retrieval, and count
mismatches fail the check. Results are written only when an output directory
outside the repository is provided. Overlap/estimated-enrollment examples must still demonstrate those properties;
histogram/network examples must be nonempty. Registry changes can make independent retrievals disagree. Files are saved after each
case passes; later failures or I/O errors can leave a partial new run.
