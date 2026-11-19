# Data and counting semantics

[API contract](architecture.md#api-contract) · [Project overview](../README.md).

These rules define how to interpret chart values and render the returned JSON.
Each chart is validated before serialization. Counts are non-negative JSON integers;
network edge weights are positive integers. Booleans, strings, and floats are not counts.

## Cartesian fields and encodings

| Grouping | Row field | X type |
| --- | --- | --- |
| `phase` | `phase` | ordinal |
| `overall_status` | `overall_status` | nominal |
| `sponsor_class` | `sponsor_class` | nominal |
| `intervention` | `intervention_type` | nominal |
| `country` | `country` | nominal |
| `start_year` | `start_year` | temporal |

Each row adds `trial_count`, encoded by quantitative `y`. Grouped bars add `cohort`,
encoded by nominal `series`; other Cartesian charts have `series: null`. Rows have
exactly the encoded fields, with no duplicate category/cohort pairs.

Annual values use `YYYY-01-01` as a rendering anchor for a year bucket, not an imputed
study start day. Render categories in array order and annual values chronologically.

## Population metadata

`retrieved_studies` is the distinct retrieved population, including studies excluded
from a particular calculation. `total_matches` is the nullable registry total for
one search. `complete_results` must be true for every successful chart.

`filters` describes the effective search, or shared filters in comparisons.
`grouping` identifies the dimension; `counting_unit` explicitly identifies the unit.
Single Cartesian charts use `unique NCT IDs per <grouping>`. Comparisons use
`unique NCT IDs per category per cohort`. Warnings describe retrieval, interpretation,
missing-data exclusions, and nonexclusive categories.

A comparison has `comparison_field` (`drug_name` or `condition`), exactly two
`cohorts`, and `overlap_studies`. Each cohort records its label, effective filters,
distinct retrieved count, nullable registry total, completeness, and retrieval warnings.
Cohort labels preserve question order. Top-level retrieved studies count the union:
`size(A) + size(B) - overlap`. The top-level registry total is null, not a sum.
Single-cohort charts have no comparison metadata. Histogram/graph summaries are
present only for their respective families; their meanings are described below.

## Counting and missing-data semantics

A study means a distinct NCT ID. Repeated records or repeated values cannot multiply
a study's contribution within a category. The adapter normally deduplicates records
first, retaining the first; pure categorical aggregation unions categories across
repeated IDs. Annual, histogram, and graph aggregation retain the first record per ID.
This difference is relevant only to callers supplying conflicting duplicate records
directly to pure builders: the HTTP pipeline always uses adapter-deduplicated inputs.
Citations bind to the captured first record and reject categories that require an
uncaptured later duplicate; they never borrow evidence to justify a unioned category.

- **Phase:** A multi-phase study counts once in each phase. `NA` is “Not applicable”;
  absent/empty phases are “Missing phase”, not NA. Order is Early Phase 1, Phase 1–4,
  Not applicable, unrecognized codes alphabetically, Missing phase last. Unknown codes
  retain their raw value in an `Unrecognized phase: ...` label.
- **Status:** Groups overall study status. Reported `UNKNOWN` is retained separately
  from “Missing status”.
- **Sponsor class:** Groups **lead** sponsor class, retaining reported codes. Missing
  data becomes “Missing lead sponsor class”. This differs from the sponsor search,
  which includes collaborators as well as sponsors.
- **Intervention:** Groups reported intervention **types**, not drug names. A study
  with several DRUG entries counts once under DRUG. Multiple types count separately.
  No interventions or an intervention without a type contributes to “Missing
  intervention type”; a study may have both known and missing intervention types.
- **Country:** Uses reported trial-location countries, not sponsor country. Repeated
  sites in a country count once; a multinational study counts once in each country.
  Studies with no reported country appear as “Missing country”. Filtering to one
  country selects studies with that location; their other countries remain in the
  distribution. It does not restrict the chart to that one country.
- **Year:** Uses the normalizer's validated study **start year**, handling year-only,
  year-month, and full dates. Missing/invalid dates are excluded with an explicit
  omitted-study count in warnings. Retrieved counts still include excluded studies.
  No posting/completion dates are substituted; only observed years appear. Empty
  annual data can mean all retrieved studies lacked usable dates, not just no matches.

Non-phase categorical codes/country names are retained as reported and sorted
lexicographically, with missing labels last. Unknown codes are not guessed or
collapsed into missing data. Category totals may exceed unique studies for phases,
interventions, and countries; warnings explain the non-exclusive counting.

Bar/time-series charts do not invent zero categories or absent years.
Histogram fixed-bin zeros have the separately documented semantics below. Comparison categories
are the union of observed labels across both cohorts, aligned with one row per
cohort. **Zero means that category is absent from a completely retrieved cohort.**
Both cohorts empty produces no rows. Failure or incompleteness in either cohort
fails the whole comparison; it cannot create a zero or a partial comparison.

## Enrollment histogram contract

Supported plan: `distribution / study_count / enrollment_count / histogram`, with no
comparison. Natural language must ask for the **distribution of enrollment sizes**;
averages, sums of participants, outcomes, and histogram comparisons are unsupported.
The normalizer reads `protocolSection.designModule.enrollmentInfo.count` and `.type`.
The [normalizer fixture](../server/tests/fixtures/README.md) supplies synthetic inputs.

Only non-negative integer counts are valid: zero is included; missing values,
negative values, floats, numeric strings, booleans, and malformed fields are excluded.
Each NCT ID is counted once, retaining the first record on duplicates. Missing or
unrecognized enrollment **type** does not invalidate a valid count; it is disclosed.
Reported ACTUAL and ESTIMATED counts share the histogram, with their included-study
counts recorded separately. Estimates are **not** represented as observed enrollment.

The deterministic bins are `[0,10)`, `[10,50)`, `[50,100)`, `[100,500)`, `[500,1000)`,
`[1000,5000)`, and `[5000,infinity)`: lower bound inclusive, upper bound exclusive.
`visualization.data` rows have exactly:

| Field | Type / meaning |
| --- | --- |
| `bin_label` | Human-readable integer range: 0–9, 10–49, 50–99, 100–499, 500–999, 1000–4999, 5000+ |
| `bin_lower` | Non-negative integer, inclusive |
| `bin_upper` | Non-negative integer, exclusive; null means no upper bound |
| `trial_count` | Non-negative integer **study count**, never a participant total |

`encoding.x` encodes quantitative `bin_lower`; `x2` encodes quantitative `bin_upper`;
`y` encodes quantitative `trial_count`. The final null upper bound is explicitly
open-ended; its label is 5000+. No frontend-specific scale, clipping, or layout is assumed.
When at least one valid count exists, all seven ordered bins are emitted. Zero bins
mean no **included valid-count studies** in that range, not guessed values for missing
enrollment. Empty or entirely invalid datasets produce `data: []` with exclusions.

`meta.grouping` is `enrollment_count`; `counting_unit` is
`unique NCT IDs per enrollment bin`. `meta.histogram` contains `included_studies`,
`excluded_studies`, `actual_studies`, `estimated_studies`, and `unknown_type_studies`.
Type counts refer only to included studies. Validation enforces that bin counts sum
to included studies, included plus excluded equals distinct retrieved studies, and
the type counts sum to included studies. Bin boundaries, labels, channels, and order
are validated. Retrieval must be complete before any histogram is returned.

## Lead sponsor–drug graph contract

Supported plan: `relationship / study_count / sponsor_drug / network_graph`, with no
comparison. This is one bipartite relationship type, not a general graph engine.
Sponsor nodes use the reported **lead sponsor name**; drug nodes use named interventions
whose reported type is exactly `DRUG`. `BIOLOGICAL`, device, and all other types are
excluded. Sponsor filtering still matches sponsors or collaborators, but graph nodes
always come from the lead sponsor. Intervention search selects studies, not graph
nodes: a searched intervention typed BIOLOGICAL may be absent from the graph.

The graph's `data` is an object containing typed arrays:

| Object | Fields |
| --- | --- |
| Node | `id`: stable string; `label`: exact normalized source label; `entity_type`: `sponsor` or `drug` |
| Edge | `source`: sponsor node ID; `target`: drug node ID; `weight`: positive integer distinct-study count |

IDs are `sponsor:` or `drug:` plus the full SHA-256 hex digest of the UTF-8 source
label. Type prefixes distinguish identical sponsor/drug names. The normalizer trims
outer whitespace; there is no case folding, synonym matching, pharmacological identity
inference, or LLM entity resolution. Different reported labels remain different nodes.
Repeated named DRUG entries do not multiply edge weights; repeated study records use
the adapter's first-record-wins policy. A study with several eligible drugs can support
several edges, so edge weights must not be summed as unique studies.

`encoding` names `node_id` (`id`), `node_label` (`label`), `node_type` (`entity_type`),
`source` (`source`), `target` (`target`), and `weight` (`weight`). All are nominal except
quantitative `weight`, titled “Distinct supporting studies”. Node channels refer to
`data.nodes`; edge channels refer to `data.edges`. No X/Y axes or layout are implied.
Edges describe **within-study co-occurrence in registry records**, not commercial
partnerships, efficacy, causal relationships, or relationships inferred across studies.

Nodes are sorted by ID. Edges sort by weight descending, then exact sponsor label and
drug label ascending. Display is capped at the first **200 edges**, with only their
connected nodes (at most 400). Full edge weights and eligibility diagnostics are
computed before this display cap. Nodes without eligible edges are not fabricated.
An empty/ineligible cohort returns `{nodes: [], edges: []}`.

`meta.grouping` is `sponsor_drug`; `counting_unit` is
`unique NCT IDs per lead sponsor-drug edge`. `meta.graph` contains:

- `included_studies` and `excluded_studies`: distinct studies contributing at least
  one eligible edge before the display cap, or no edge, respectively. They partition
  `retrieved_studies`.
- `missing_sponsor_studies` and `no_named_drug_studies`: unique-study diagnostics that
  can overlap; do not sum them as exclusions.
- `unnamed_drug_studies`: studies with at least one unnamed DRUG entry. Those entries
  are skipped, but other named DRUG entries can still contribute edges.
- `total_nodes`, `total_edges`, `displayed_nodes`, `displayed_edges`, `omitted_nodes`,
  `omitted_edges`, `display_truncated`, and `edge_limit` (200).

`complete_results` continues to mean complete **study retrieval**. A display-limited
graph may have `complete_results: true` and `graph.display_truncated: true`; omissions
and warnings explain the difference. Incomplete upstream retrieval still fails closed.
Validators enforce stable IDs, bipartite endpoints, distinct positive-weight edges,
connected nodes, order, cap/omission totals, and consistent retrieval/exclusion metadata.


## Optional source evidence

`include_citations: true` adds separate datum-to-study evidence without changing
these counting rules. Source array positions and raw values are retained while
membership helpers are shared with counting. See [the citation contract](citations.md)
for missing-value proof, comparison zeros, graph node links, and fail-closed limits.
