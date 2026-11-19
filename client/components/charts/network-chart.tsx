"use client";

import { useEffect, useRef, useState } from "react";
import type { NetworkVisualization } from "@/lib/types";

export default function NetworkChart({ chart }: { chart: NetworkVisualization }) {
  const container = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState(
    "Select an edge to inspect its supporting-study count.",
  );
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let disposed = false;
    let cleanup: (() => void) | undefined;

    import("cytoscape")
      .then(({ default: cytoscape }) => {
        if (disposed || !container.current) return;
        const graph = cytoscape({
          container: container.current,
          elements: [
            ...chart.data.nodes.map((node) => ({ data: { ...node } })),
            ...chart.data.edges.map((edge, i) => ({ data: { ...edge, id: `edge_${i}` } })),
          ],
          style: [
            {
              selector: "node",
              style: {
                label: "data(label)",
                color: "#dbe4f1",
                "font-size": 10,
                "text-wrap": "wrap",
                "text-max-width": "100px",
                "text-valign": "bottom",
                "text-margin-y": 6,
                width: 22,
                height: 22,
                "background-color": "#568ee3",
              },
            },
            {
              selector: 'node[entity_type = "sponsor"]',
              style: { shape: "round-rectangle", "background-color": "#d4a35b" },
            },
            {
              selector: "edge",
              style: { width: 2, "line-color": "#4d5a70", "curve-style": "bezier" },
            },
            {
              selector: ":selected",
              style: { "background-color": "#ffffff", "line-color": "#a4c6ff" },
            },
          ],
          layout: { name: "cose", animate: false, randomize: false, padding: 40 },
          minZoom: 0.15,
          maxZoom: 3,
          wheelSensitivity: 0.2,
        });

        graph.on("tap", "edge", (event) => {
          const edge = event.target;

          setSelection(
            `${edge.source().data("label")} → ${edge.target().data("label")}: ${edge.data("weight")} distinct supporting studies`,
          );
        });
        const observer = new ResizeObserver(() => {
          graph.resize();
          graph.fit(undefined, 40);
        });

        observer.observe(container.current);
        cleanup = () => {
          observer.disconnect();
          graph.destroy();
        };
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [chart]);
  const labels = new Map(chart.data.nodes.map((n) => [n.id, n.label]));

  return (
    <>
      <p className="chart-note">
        <span className="legend sponsor" /> Lead sponsor <span className="legend drug" /> Reported
        DRUG · co-occurrence, not causality
      </p>
      {!failed && (
        <div
          className="network"
          ref={container}
          role="img"
          aria-label="Interactive lead sponsor–drug co-occurrence graph"
        />
      )}
      <p className="chart-note" aria-live="polite">
        {failed ? "Interactive graph unavailable. Read the relationships below." : selection}
      </p>
      <details className="data-table">
        <summary>View relationships and weights</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Lead sponsor</th>
                <th>Drug</th>
                <th>Supporting studies</th>
              </tr>
            </thead>
            <tbody>
              {chart.data.edges.map((e, i) => (
                <tr key={i}>
                  <td>{labels.get(e.source)}</td>
                  <td>{labels.get(e.target)}</td>
                  <td>{e.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
