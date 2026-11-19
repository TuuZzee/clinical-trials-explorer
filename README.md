# Clinical Trials Explorer

An experimental full-stack project for exploring ClinicalTrials.gov studies through natural-language questions, interactive visualizations, and verifiable source citations.

Built with Next.js, React, Python, and FastAPI, the application combines AI-assisted query interpretation with deterministic data retrieval and aggregation.

Disclaimer: This project was developed for learning, experimentation, and technical demonstration purposes. It is not a clinically validated product and is not intended for medical decision-making, regulatory analysis, commercial applications, or production use. ClinicalTrials.gov records may change over time, and search results do not necessarily establish exact clinical or drug identities.

## Capabilities

- Ask a natural-language question or start from one of five preset queries. Add
  explicit condition, intervention, sponsor, country, status, or start-year filters.
- View five chart types in the Next.js interface. It shows the effective filters,
  retrieval counts, and warnings alongside the chart. Questions run independently,
  and reloading clears the page's question and result history.
- Retrieve matching studies from ClinicalTrials.gov with bounded pagination.
  Python deduplicates the records and computes the counts. Incomplete retrievals
  return an error.
- Enable citations to inspect the contributing study IDs, original registry field
  paths, and exact reported values behind a chart row or network edge.

| Visualization | Supported analysis |
| --- | --- |
| Bar chart | Phase, status, sponsor class, intervention type, or trial location |
| Time series | Study starts by year |
| Grouped bar chart | Two named drugs or conditions across a supported category |
| Histogram | Distribution of reported enrollment sizes |
| Network graph | Lead sponsors linked to named drug interventions |

## Technology stack

- **Client** — Next.js, React, TypeScript, Recharts, and Cytoscape.js.
- **Server** — Python, FastAPI, OpenAI Responses API, and ClinicalTrials.gov Data API v2.
- **Verification** — pytest, Ruff, and Playwright.

## Getting started

Use Python 3.12+ with [uv](https://docs.astral.sh/uv/) and Node.js 20.9+ with npm.

### Start FastAPI

Run these commands from the repository root.

```sh
cd server
uv sync --locked --extra dev --python 3.12
cp -n .env.example .env
# Set OPENAI_API_KEY in .env to enable live queries.
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Live queries require an OpenAI API key and may incur API charges. Keep the key in
`server/.env` or the backend process environment. See the
[configuration reference](docs/architecture.md#configuration) for `OPENAI_MODEL`
and other settings. FastAPI can run independently, with interactive
[API docs](http://127.0.0.1:8000/docs) and a [health endpoint](http://127.0.0.1:8000/health).

### Start Next.js

Run these commands in a second terminal at the repository root.

```sh
cd client
npm ci
cp -n .env.example .env.local
npm run dev
```

Open <http://127.0.0.1:3000>. The Next.js proxy uses the server-only
`BACKEND_URL`, which defaults to `http://127.0.0.1:8000`.

## How it works

```text
Question → constrained OpenAI plan → validated registry retrieval
  → deterministic Python aggregation → visualization JSON → Next.js rendering
```

OpenAI interprets the question as a typed plan. The backend validates the plan,
applies explicit filters, and retrieves registry records. Python calculates every
chart value from those records using documented counting rules. Citation requests
capture evidence from the same registry pages.

## Example questions

Choose an example in the client to fill the question and filters, then submit it
to query the live registry. The five choices cover phase distribution, annual
trends, two-drug comparison, enrollment sizes, and sponsor–drug relationships.
The interactive [API docs](http://127.0.0.1:8000/docs) also provide sample requests.

## Quality and limitations

The backend suite mocks OpenAI and the registry to test planning, paging, counting,
and exact citations. Browser tests use synthetic responses to exercise all five
visualizations through the production build and proxy. See
[verification](docs/verification.md) for local checks and CI coverage.

- Registry matching follows ClinicalTrials.gov search semantics. Sponsor search also
  includes collaborators, and intervention terms do not guarantee exact drug identity.
- Retrieval is capped at 5,000 raw records or 50 pages per cohort by default. Broad
  queries may need narrower filters. Unsupported analytics return explicit errors.
- Registry records can change during and after retrieval. A source link may show
  an updated value when opened later.
- The application runs locally and has no built-in authentication.

## Documentation

- [Architecture, API contract, and configuration](docs/architecture.md)
- [Data and counting semantics](docs/data-and-counting.md)
- [Source citations and evidence limits](docs/citations.md)
- [Next.js interface and browser checks](client/README.md)
- [ClinicalTrials.gov Data API](https://clinicaltrials.gov/data-api/api)

## Integrity Note

Clinical-trial data are retrieved from ClinicalTrials.gov. At runtime, the OpenAI
API interprets questions into structured plans. Python retrieves the registry
records and deterministically computes every chart value, including study counts.

The application is intended for technical exploration and demonstration. Registry
data may change, and results should be interpreted as registry-derived counts with
no clinical validation.
