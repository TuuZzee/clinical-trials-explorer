import type { ResultResponse, VisualizationRequest } from "./types";

const object = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const strings = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((x) => typeof x === "string");

const count = (v: unknown) => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;

const channel = (v: unknown) =>
  object(v) && typeof v.field === "string" && typeof v.title === "string";

// Focused transport guards, not a second implementation of the backend validators.
export function isResult(v: unknown): v is ResultResponse {
  if (!object(v) || !object(v.visualization) || !object(v.meta)) return false;
  const c = v.visualization,
    m = v.meta;

  if (
    typeof c.title !== "string" ||
    !object(c.encoding) ||
    !object(m.filters) ||
    !count(m.retrieved_studies) ||
    typeof m.source !== "string" ||
    typeof m.grouping !== "string" ||
    typeof m.counting_unit !== "string" ||
    typeof m.complete_results !== "boolean" ||
    (m.warnings != null && !strings(m.warnings)) ||
    (m.cohorts != null &&
      (!Array.isArray(m.cohorts) ||
        !m.cohorts.every(
          (x) =>
            object(x) &&
            typeof x.label === "string" &&
            count(x.retrieved_studies) &&
            object(x.filters) &&
            (x.warnings == null || strings(x.warnings)),
        )))
  )
    return false;

  if (
    m.histogram != null &&
    (!object(m.histogram) ||
      ![
        "included_studies",
        "excluded_studies",
        "actual_studies",
        "estimated_studies",
        "unknown_type_studies",
      ].every((k) => count((m.histogram as Record<string, unknown>)[k])))
  )
    return false;

  if (
    m.graph != null &&
    (!object(m.graph) ||
      ![
        "displayed_edges",
        "total_edges",
        "omitted_edges",
        "omitted_nodes",
        "included_studies",
        "excluded_studies",
      ].every((k) => count((m.graph as Record<string, unknown>)[k])))
  )
    return false;

  if (c.type === "network_graph") {
    if (
      !object(c.data) ||
      !Array.isArray(c.data.nodes) ||
      !Array.isArray(c.data.edges) ||
      !c.data.nodes.every(
        (n) =>
          object(n) &&
          typeof n.id === "string" &&
          typeof n.label === "string" &&
          ["sponsor", "drug"].includes(String(n.entity_type)),
      ) ||
      !c.data.edges.every(
        (e) =>
          object(e) &&
          typeof e.source === "string" &&
          typeof e.target === "string" &&
          count(e.weight),
      )
    )
      return false;
  } else {
    if (
      !["bar_chart", "time_series", "grouped_bar_chart", "histogram"].includes(String(c.type)) ||
      !channel(c.encoding.x) ||
      !channel(c.encoding.y) ||
      !Array.isArray(c.data)
    )
      return false;
    const x = (c.encoding.x as Record<string, string>).field;
    const y = (c.encoding.y as Record<string, string>).field;

    if (
      !c.data.every(
        (row) =>
          object(row) &&
          count(row[y]) &&
          (c.type === "histogram"
            ? typeof row.bin_label === "string"
            : typeof row[x] === "string") &&
          (c.type !== "grouped_bar_chart" || typeof row.cohort === "string"),
      )
    )
      return false;
  }

  if (v.citations != null) {
    if (
      !object(v.citations) ||
      !count(v.citations.reference_count) ||
      !Array.isArray(v.citations.datums) ||
      (v.citations.nodes != null &&
        (!Array.isArray(v.citations.nodes) ||
          !v.citations.nodes.every(
            (n) => object(n) && typeof n.node_id === "string" && strings(n.edge_paths),
          ))) ||
      !v.citations.datums.every(
        (d) =>
          object(d) &&
          typeof d.datum_path === "string" &&
          count(d.supporting_study_count) &&
          ["source_fields", "complete_cohort_zero"].includes(String(d.basis)) &&
          Array.isArray(d.studies) &&
          d.studies.every(
            (s) =>
              object(s) &&
              typeof s.nct_id === "string" &&
              typeof s.study_url === "string" &&
              Array.isArray(s.fields) &&
              s.fields.every(
                (f) =>
                  object(f) &&
                  typeof f.path === "string" &&
                  (f.kind === "absent"
                    ? object(f.ancestor) &&
                      typeof f.ancestor.path === "string" &&
                      (f.ancestor.kind === "object_keys"
                        ? strings(f.ancestor.keys)
                        : "value" in f.ancestor)
                    : ["value", "null", "empty", "invalid"].includes(String(f.kind)) &&
                      "value" in f),
              ),
          ),
      )
    )
      return false;
  }

  return true;
}

export async function visualize(
  payload: VisualizationRequest,
  signal: AbortSignal,
): Promise<ResultResponse> {
  const reply = await fetch("/api/visualize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
    cache: "no-store",
  });
  let body: unknown;

  try {
    body = await reply.json();
  } catch {
    throw new Error("The service returned an unreadable response. Please retry.");
  }

  if (!reply.ok) {
    if (object(body) && object(body.error) && typeof body.error.message === "string") {
      const hint =
        body.error.code === "citation_limit_exceeded"
          ? " Narrow the query or turn off source citations."
          : "";

      throw new Error(body.error.message + hint);
    }

    throw new Error("The request failed. Check the backend and try again.");
  }

  if (!isResult(body))
    throw new Error("The backend returned an unsupported or malformed chart response.");

  return body;
}
