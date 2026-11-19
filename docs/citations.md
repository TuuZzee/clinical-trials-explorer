# Optional source citations

[Project overview](../README.md#capabilities).

Set `include_citations: true` on a visualization request to trace each returned
count to the records and exact source fields that supported it. The default is
false; omitted and explicit false produce the ordinary response with no `citations`
key. Unknown/string/numeric/null flag values are rejected.

The planner receives the question only, as before. This flag is neither a search
filter nor an instruction to the model. Python builds citations deterministically
from the same registry pages used for aggregation, without per-study re-fetches in
the request pipeline. Citation mode changes neither filters nor counts.

## Response contract

The cited response keeps `visualization` and `meta` unchanged and adds a typed
`citations` bundle. The ordinary and cited responses are explicit separate Pydantic
models. The endpoint advertises both in OpenAPI and validates the final serialized
response before returning it.

The bundle contains:

| Field | Meaning |
| --- | --- |
| `source` | `ClinicalTrials.gov Data API v2` |
| `reference_count` | Total distinct datum-to-study contributions |
| `datums` | One citation entry per returned row or displayed graph edge, in data order |
| `nodes` | Graph node IDs linked to the displayed edge citation paths establishing their labels |

A datum entry has `datum_path`, optional `cohort`, `basis`,
`supporting_study_count`, and `studies`. Paths are JSON Pointers into the returned
chart: `/visualization/data/0` for a row and `/visualization/data/edges/0` for an edge.
Each study has `nct_id`, canonical `study_url`, and `fields`. IDs are unique and sorted
within a datum; their count must equal the chart count or edge weight.

This synthetic example shows the response shape.

```json
{
  "citations": {
    "source": "ClinicalTrials.gov Data API v2",
    "reference_count": 1,
    "datums": [{
      "datum_path": "/visualization/data/0",
      "cohort": null,
      "basis": "source_fields",
      "supporting_study_count": 1,
      "studies": [{
        "nct_id": "NCT00000001",
        "study_url": "https://clinicaltrials.gov/study/NCT00000001",
        "fields": [
          {"kind": "value", "path": "/protocolSection/identificationModule/nctId", "value": "NCT00000001"},
          {"kind": "value", "path": "/protocolSection/designModule/phases/0", "value": "PHASE2"}
        ]
      }]
    }],
    "nodes": []
  }
}
```

The supporting study URL identifies the registry study, not an immutable version of
its record. Evidence describes the response used by this request, which may differ
from a later registry fetch.

## Exact evidence and missing fields

Source paths are relative to each individual study object, not the search-page
`studies` array. They refer to original records, including original array positions. Values
retain their JSON types, spelling, whitespace, and collection order. Normalized
labels are not substituted for source values. Every contribution includes the source
NCT ID field in addition to the fields establishing its category or relationship.

A present-field entry contains `path`, `kind`, and the exact `value`:

- `value`: a present, non-null, nonempty source value.
- `null`: an explicit source JSON null.
- `empty`: an actual empty string, list, or object.
- `invalid`: a present value/container unusable for the relevant normalized field.

Missing evidence has `kind: absent`, the expected field `path`, and an `ancestor`.
It has **no invented `value: null`** at the missing path. The ancestor is either:

- `{kind: object_keys, path, keys}`: the complete, sorted observed key set of the
  nearest existing source object. The missing next key is absent from that set.
  This is explicitly a structural excerpt, not a fabricated object or literal field value.
- A source-value excerpt at the nearest existing ancestor that blocks traversal,
  such as a null or non-object module. Its original value is preserved.

For example, an absent `leadSponsor.class` beneath an explicit null `leadSponsor`
is represented as an absent class with the null ancestor. It does not claim that
`class: null` appeared in the record. Empty or malformed arrays can be quoted as
bounded excerpts to explain missing categories. Structural key excerpts avoid
exporting unrelated ancestor values or a complete study record.

## Contribution rules

Counts and citations use shared membership helpers. Retrieval retains the first raw
record per NCT ID; duplicate pages cannot supply a different evidence record later.
The evidence store is request-local and keyed separately within each cohort.

| Grouping | Evidence |
| --- | --- |
| Phase | Original phase array elements; multiple phases may cite the same study in separate rows. Missing, NA, and unrecognized phases remain distinct. |
| Status | Exact `statusModule.overallStatus`, or its missing/empty/invalid evidence. |
| Sponsor class | Exact `sponsorCollaboratorsModule.leadSponsor.class`, not collaborator class. |
| Intervention type | Original `armsInterventionsModule.interventions/<index>/type` fields establishing each type. Repeated entries add evidence, not counts. |
| Country | Original `contactsLocationsModule.locations/<index>/country` fields. Repeated sites count once per country; multinational studies support multiple rows. |
| Annual trend | Original `statusModule.startDateStruct.date`. The chart's `YYYY-01-01` remains an annual rendering anchor. |
| Enrollment bin | Original enrollment count and type (or genuine absence of type). The count must belong to the displayed bin; ACTUAL and ESTIMATED remain distinguishable. |
| Network edge | Lead sponsor name plus each matching original DRUG name/type pair. Weight equals distinct supporting IDs; repeated drug entries do not multiply it. |

Comparison identities include the cohort label. An overlapping study can support
both series, using the record retrieved for each search. This establishes membership
in registry search results, not exact pharmacological or condition identity.

Zero rows have `basis: complete_cohort_zero`, no studies, and supporting count zero.
Their meaning comes from complete retrieval and category/bin alignment, not a source
record reporting a zero. Histogram zero bins refer only to studies with valid counts.

Graph nodes link explicitly to their supporting displayed edges through `nodes`.
They do not duplicate study references or consume additional contribution slots.
The existing 200-edge display cap is unchanged: every displayed edge has complete
evidence; omitted edges need no citations. Population completeness, graph omissions,
and display-truncation warnings retain their existing meanings.

## Limits and failures

| Setting | Default | Meaning |
| --- | --- | --- |
| `CITATION_MAX_REFERENCES` | 1000 | Maximum unique datum-to-study contributions, across all cohorts |
| `CITATION_MAX_BYTES` | 1000000 | Bound on captured selected-source evidence and on the serialized citation bundle |

One study in two phase rows is two references; one study supporting two edges is two.
Repeated fields within a single contribution are still one reference. Node-to-edge
links are not study contributions. Limits never affect citation-disabled requests.

The contribution count is checked before constructing the study citation payload.
Exact raw evidence is also size-checked before copying it into the sidecar; this
budget is shared across cohorts. The complete bundle is size-checked again, including
row/node identities and metadata. Sizing uses compact UTF-8 JSON, conservatively for
numeric formatting differences. A source-capture limit may reject an otherwise small
displayed graph because evidence for eligible but eventually undisplayed edges was
captured before display selection. This bounds memory without re-fetching records.

Either resource limit rejects the entire request with HTTP 422:

```json
{"error":{"code":"citation_limit_exceeded","message":"Citation limit exceeded. Narrow the filters or disable citations."}}
```

There is no partial or sampled citation success. Invalid source binding or output
consistency produces sanitized HTTP 500 `invalid_citations`. Incomplete registry
retrieval keeps its existing error; citations cannot turn it into a partial chart.

Only selected grouping fields are retained, not full raw records. Source bindings
carry internal mutation-detection hashes and are compared with their normalized
grouping inputs. These hashes are consistency guards, not signed registry attestations.
Construction checks bindings once; final validation
checks source identity, cohort membership, selected fields, datum coverage, distinct
IDs, count/weight agreement, zero semantics, and graph node links after serialization.
Evidence is never emitted when these checks fail.

Citation work runs under the same request deadline in the existing worker thread.
As with ordinary aggregation, cancelling the await cannot forcibly terminate already
running Python work. The byte/reference caps bound the additional evidence retained
and returned. DEBUG events contain counts and failure categories only, not excerpts.

## Verification

The offline tests traverse original fixture records and verify quoted JSON Pointers,
values, ancestor keys, and contributor IDs across all chart families. They cover
missing/null/empty/malformed fields, source whitespace, original indices, unknown
codes, overlapping cohorts, aligned zeros, histogram boundaries, weighted graphs,
display truncation, exact limits, oversized excerpts, corruption, disabled mode,
deadlines, and source-free logging.

Run the focused opt-in live check from `server/`. It compares a narrow cited phase
response with an ordinary response, independently retrieves its population, fetches
raw records by NCT ID, and checks paths, exact values, phase membership, and ancestor
evidence.

```sh
uv run python -m scripts.smoke_citations --live --base-url http://127.0.0.1:8000
```

It makes two model-backed requests plus public registry requests. Use `--output-dir`
to save fresh verification artifacts outside the repository. Without `--live`, it
shows help. A source change between requests can cause verification to fail.

The independent check includes a fresh population retrieval and raw per-study GETs.
These additional calls run only in the verification tool.
