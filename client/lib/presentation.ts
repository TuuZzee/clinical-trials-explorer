import type { CartesianVisualization, DatumCitation, Visualization } from "./types";

export function groupedRows(chart: CartesianVisualization, labels: string[]) {
  const categories = new Map<string, Record<string, string | number>>();

  for (const row of chart.data) {
    const category = String(row[chart.encoding.x.field]);

    if (!categories.has(category)) categories.set(category, { category });
    const index = labels.indexOf(String(row.cohort));

    if (index >= 0)
      categories.get(category)![`series_${index}`] = row[chart.encoding.y.field] as number;
  }

  return [...categories.values()]; // Presentation pivot only: no summing or zero-filling.
}

export function datumLabel(chart: Visualization, datum: DatumCitation): string {
  if (chart.type === "network_graph") {
    const match = /^\/visualization\/data\/edges\/(\d+)$/.exec(datum.datum_path);
    const edge = match ? chart.data.edges[Number(match[1])] : undefined;

    if (!edge) return datum.datum_path;
    const labels = new Map(chart.data.nodes.map((n) => [n.id, n.label]));

    return `${labels.get(edge.source) ?? edge.source} → ${labels.get(edge.target) ?? edge.target}`;
  }

  const match = /^\/visualization\/data\/(\d+)$/.exec(datum.datum_path);
  const row = match ? chart.data[Number(match[1])] : undefined;

  if (!row) return datum.datum_path;
  const label =
    chart.type === "histogram"
      ? row.bin_label
      : (row as Record<string, unknown>)[chart.encoding.x.field];

  return `${chart.type === "time_series" ? String(label).slice(0, 4) : label}${datum.cohort ? ` · ${datum.cohort}` : ""}`;
}
