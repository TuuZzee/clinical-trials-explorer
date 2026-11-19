# Clinical Trials Explorer client

The Next.js interface for exploring ClinicalTrials.gov studies with natural-language
questions, interactive charts, and optional source citations. Each question runs
independently. React keeps question and result history in the current page session,
and reloading clears it.

## Run

Use Node.js 20.9+ (verified with Node 24.22.1) and npm. Follow the
[backend setup](../README.md#getting-started) and start FastAPI from `server/`.

In a separate terminal at the repository root:

```sh
cd client
npm ci
cp -n .env.example .env.local
npm run dev
```

Open <http://127.0.0.1:3000>. `BACKEND_URL` is server-only and defaults to
`http://127.0.0.1:8000`. Keep `OPENAI_API_KEY` in the backend environment. No
`NEXT_PUBLIC_` credentials or browser-to-registry/model requests are used.

`POST /api/visualize` forwards to FastAPI's `/api/v1/visualize`, preserving its
status and body with caching disabled. Connection failures use a sanitized 502;
the proxy deadline is 150 seconds to accommodate the default 120-second backend
budget. Raising the backend budget above that requires reviewing the proxy deadline.

For production-mode local testing, run `npm run build` then `npm start`.
No deployment configuration is included. The `dev`, `build`, and `start` scripts set
`NEXT_TELEMETRY_DISABLED=1` to disable [Next.js usage telemetry](https://nextjs.org/telemetry).

## Interface

- Enter submits; Shift+Enter adds a newline. Submissions are locked while loading,
  and failed questions remain in the composer for editing.
- Five example questions fill the composer and filters for a live query.
- Filters apply to the next independent request. Selecting another starter replaces
  filters, including clearing a singular comparison-axis filter. Backend validation
  remains authoritative.
- Recharts renders categorical bars, annual trends, grouped comparisons and labeled
  enrollment bins. Grouped rows are pivoted without summing or filling values.
  Unequal enrollment ranges are displayed as equally spaced labeled categories;
  `5000+` remains open-ended. Annual anchors display only the year.
- Cytoscape uses the backend's IDs and weights. Sponsors and drugs have distinct
  styles; selecting an edge shows support. The relationship table is also available
  without the graph. Graph instances and resize observers are cleaned up on unmount.
- Metadata discloses effective filters, retrieval counts, warnings, cohort overlap,
  enrollment types/exclusions and graph display omissions.
- Optional sources show each datum/edge's studies, original paths and JSON values,
  including actual absence ancestors. Node links are not additional study references.

## Verify

Run these commands from `client/`. Typechecking generates the Next.js types first.

```sh
npm run typecheck
npm run lint
npm run format
npm run build
npx playwright install chromium
npm test
```

If Chrome is already installed, `PLAYWRIGHT_CHANNEL=chrome npm test` can use it
instead. The browser suite starts the built app on port 3000 and a test-only HTTP
upstream on 9010. Keep these ports free. Tests use
[synthetic visualization and evidence fixtures](tests/fixtures/README.md) through
the real proxy. They exercise all five renderers, aligned zeros, weighted edges,
source links and missing evidence, keyboard and loading behavior, session history,
mobile width, malformed results, structured errors, and upstream disconnection.
Failure traces stay under ignored `test-results/`.

## Limits

This is a local demonstration client, without authentication, streaming, persistence,
or conversation management. Larger results and many expanded source records can
produce substantial DOM/state memory. Long categorical charts scroll horizontally.
No general mobile screen-reader or cross-browser accessibility audit has been done;
labeled controls, keyboard submission, data tables and status announcements are included.
Unmounting aborts the browser request but does not guarantee immediate backend cancellation.
The network uses a basic automatic layout and a readable table fallback.

ESLint 9 is retained because the Next lint plugin peers do not yet accept ESLint 10.
No forced major downgrade or peer-dependency bypass is applied.
