"use client";

import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { Visualization, VisualizationMeta } from "@/lib/types";
import { groupedRows } from "@/lib/presentation";
import NetworkChart from "./network-chart";

export default function ChartRenderer({
  chart,
  meta,
}: {
  chart: Visualization;
  meta: VisualizationMeta;
}) {
  if (chart.type === "network_graph")
    return chart.data.edges.length ? (
      <NetworkChart chart={chart} />
    ) : (
      <p className="empty-result">No eligible sponsor–drug relationships in these results.</p>
    );

  if (!chart.data.length)
    return (
      <p className="empty-result">
        No chartable data for this request. Check the metadata for exclusions or try different
        filters.
      </p>
    );
  const histogram = chart.type === "histogram";
  const grouped = chart.type === "grouped_bar_chart";
  const annual = chart.type === "time_series";
  const labels = grouped
    ? meta.cohorts?.length
      ? meta.cohorts.map((c) => c.label)
      : [...new Set(chart.data.map((r) => String(r.cohort)))]
    : [];
  const rows: Record<string, string | number | null>[] = chart.data.map((row) => ({ ...row }));
  const data = grouped ? groupedRows(chart, labels) : rows;
  const x = grouped ? "category" : histogram ? "bin_label" : chart.encoding.x.field;
  const y = chart.encoding.y.field;
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="#28303b" />
      <XAxis
        dataKey={x}
        tick={{ fill: "#aeb8c8", fontSize: 12 }}
        tickLine={false}
        axisLine={false}
        interval={0}
        height={65}
        tickFormatter={(v) => (annual ? String(v).slice(0, 4) : String(v))}
      />
      <YAxis
        allowDecimals={false}
        tick={{ fill: "#aeb8c8", fontSize: 12 }}
        tickLine={false}
        axisLine={false}
        width={40}
      />
      <Tooltip
        contentStyle={{
          background: "#202630",
          border: "1px solid #485364",
          borderRadius: 8,
          color: "#f2f5fa",
        }}
        labelFormatter={(v) => (annual ? String(v).slice(0, 4) : String(v))}
      />
    </>
  );

  return (
    <>
      <p className="chart-note">
        {annual
          ? "Study start year · annual anchors, not exact start dates"
          : histogram
            ? "Study counts by predefined enrollment ranges · ACTUAL and ESTIMATED reports may be combined"
            : `${chart.encoding.x.title} · ${chart.encoding.y.title}`}
      </p>
      <div className="chart-scroll">
        <div
          className="cartesian"
          style={{ minWidth: Math.max(300, data.length * (grouped ? 105 : 72)) }}
          role="img"
          aria-label={chart.title}
        >
          <ResponsiveContainer width="100%" height="100%" minWidth={1}>
            {annual ? (
              <LineChart data={data} margin={{ top: 20, right: 24, bottom: 0, left: 0 }}>
                {axes}
                <Line
                  type="linear"
                  dataKey={y}
                  name="Studies"
                  stroke="#70a7ff"
                  strokeWidth={2}
                  dot={{ r: 5 }}
                  isAnimationActive={false}
                />
              </LineChart>
            ) : (
              <BarChart data={data} margin={{ top: 20, right: 24, bottom: 0, left: 0 }}>
                {axes}
                {grouped && <Legend />}
                {grouped ? (
                  labels.map((label, i) => (
                    <Bar
                      key={label}
                      dataKey={`series_${i}`}
                      name={label}
                      fill={i ? "#d4a35b" : "#568ee3"}
                      radius={[5, 5, 0, 0]}
                      maxBarSize={90}
                      isAnimationActive={false}
                    />
                  ))
                ) : (
                  <Bar
                    dataKey={y}
                    name="Studies"
                    fill="#477fce"
                    radius={[6, 6, 0, 0]}
                    maxBarSize={120}
                    isAnimationActive={false}
                  />
                )}
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>
      </div>
      <details className="data-table">
        <summary>View chart data</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{histogram ? "Enrollment range" : chart.encoding.x.title}</th>
                {grouped && <th>Cohort</th>}
                <th>Studies</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  <td>
                    {histogram
                      ? row.bin_label
                      : annual
                        ? String(row[chart.encoding.x.field]).slice(0, 4)
                        : String(row[chart.encoding.x.field])}
                  </td>
                  {grouped && <td>{String(row.cohort)}</td>}
                  <td>{row[y]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
