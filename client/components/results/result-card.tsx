import type { ResultResponse, StudyFilters } from "@/lib/types";
import ChartRenderer from "../charts/chart-renderer";
import Sources from "./sources";

function Filters({ filters }: { filters: StudyFilters }) {
  const entries = Object.entries(filters).filter(([, v]) => v != null);

  return entries.length ? (
    <dl className="filter-values">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt>{k.replaceAll("_", " ")}</dt>
          <dd>{String(v)}</dd>
        </div>
      ))}
    </dl>
  ) : (
    <p>No restrictive search filters.</p>
  );
}

export default function ResultCard({ result }: { result: ResultResponse }) {
  const { visualization: chart, meta } = result;
  const warnings = meta.warnings ?? [];
  const cohorts = meta.cohorts ?? [];

  return (
    <section className="result-card" aria-label="Query result">
      <h2>{chart.title}</h2>
      <ChartRenderer chart={chart} meta={meta} />
      <div className="badges">
        <span className="badge">ClinicalTrials.gov</span>
        <span className="badge">{meta.retrieved_studies} retrieved studies</span>
        <span className="badge">{meta.grouping.replaceAll("_", " ")}</span>
        <span className="badge">
          {meta.complete_results ? "Complete retrieval" : "Incomplete retrieval"}
        </span>
      </div>
      {meta.graph?.display_truncated && (
        <p className="notice">
          Graph display limited: {meta.graph.displayed_edges} of {meta.graph.total_edges} edges
          shown; {meta.graph.omitted_edges} edges and {meta.graph.omitted_nodes} nodes omitted.
          Study retrieval is complete.
        </p>
      )}
      <details className="metadata">
        <summary>
          Filters, warnings and result details
          {warnings.length ? ` (${warnings.length} warnings)` : ""}
        </summary>
        <p>
          {meta.source} · {meta.counting_unit}
        </p>
        <Filters filters={meta.filters} />
        {cohorts.length > 0 && (
          <>
            <p>
              Distinct union: {meta.retrieved_studies} · overlapping studies: {meta.overlap_studies}
            </p>
            {cohorts.map((c) => (
              <details key={c.label}>
                <summary>
                  {c.label}: {c.retrieved_studies} studies
                </summary>
                <Filters filters={c.filters} />
                {(c.warnings ?? []).map((w, i) => (
                  <p key={i}>{w}</p>
                ))}
              </details>
            ))}
          </>
        )}
        {meta.histogram && (
          <p>
            Included: {meta.histogram.included_studies} · excluded:{" "}
            {meta.histogram.excluded_studies} · ACTUAL: {meta.histogram.actual_studies} · ESTIMATED:{" "}
            {meta.histogram.estimated_studies} · unknown type: {meta.histogram.unknown_type_studies}
            . Estimates are not observed final enrollment.
          </p>
        )}
        {meta.graph && (
          <p>
            Eligible studies: {meta.graph.included_studies} · excluded:{" "}
            {meta.graph.excluded_studies}. Edges describe reported co-occurrence, not partnerships
            or causality.
          </p>
        )}
        {warnings.length > 0 && (
          <ul>
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </details>
      {result.citations && <Sources bundle={result.citations} chart={chart} />}
    </section>
  );
}
