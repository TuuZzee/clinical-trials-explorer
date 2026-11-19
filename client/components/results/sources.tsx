import type { CitationBundle, SourceField, StudyCitation, Visualization } from "@/lib/types";
import { datumLabel } from "@/lib/presentation";

function Field({ field }: { field: SourceField }) {
  return (
    <div className="source-field">
      <code>{field.path}</code>
      {field.kind !== "value" && <span className="badge">{field.kind}</span>}
      {field.kind === "absent" ? (
        <>
          <p>
            Field absent. Observed ancestor: <code>{field.ancestor.path || "(study root)"}</code>
          </p>
          <pre>
            {JSON.stringify(
              field.ancestor.kind === "object_keys"
                ? { kind: "object_keys", keys: field.ancestor.keys }
                : { kind: field.ancestor.kind, value: field.ancestor.value },
              null,
              2,
            )}
          </pre>
        </>
      ) : (
        <pre>{JSON.stringify(field.value, null, 2)}</pre>
      )}
    </div>
  );
}

function StudySources({ study }: { study: StudyCitation }) {
  const fields = study.fields.filter(
    (field) => field.path !== "/protocolSection/identificationModule/nctId",
  );

  return (
    <details className="study">
      <summary>
        {study.nct_id} · {fields.length} source field{fields.length === 1 ? "" : "s"}
      </summary>
      <a
        href={`https://clinicaltrials.gov/study/${encodeURIComponent(study.nct_id)}`}
        target="_blank"
        rel="noopener noreferrer"
      >
        Open {study.nct_id} on ClinicalTrials.gov ↗
      </a>
      {fields.map((field) => (
        <Field key={field.path} field={field} />
      ))}
    </details>
  );
}

export default function Sources({
  bundle,
  chart,
}: {
  bundle: CitationBundle;
  chart: Visualization;
}) {
  return (
    <details className="sources">
      <summary>Sources · {bundle.reference_count} datum-to-study references</summary>
      <p className="muted">
        Exact fields from the records used for this result. Registry records may change later. Node
        links do not add study references.
      </p>
      {bundle.datums.length === 0 && <p>No displayed data require supporting records.</p>}
      {bundle.datums.map((d) => (
        <details key={d.datum_path} className="datum">
          <summary>
            {datumLabel(chart, d)} · {d.supporting_study_count} supporting studies
          </summary>
          {d.basis === "complete_cohort_zero" ? (
            <p>
              No contributing studies. Zero comes from the completely retrieved cohort and
              category/bin alignment.
            </p>
          ) : (
            d.studies.map((study) => <StudySources key={study.nct_id} study={study} />)
          )}
        </details>
      ))}
      {(bundle.nodes ?? []).length > 0 && (
        <details className="datum">
          <summary>Graph node evidence links</summary>
          {(bundle.nodes ?? []).map((n) => (
            <p key={n.node_id}>
              <code>{n.node_id}</code>
              <br />
              Supported by {n.edge_paths.join(", ")}
            </p>
          ))}
        </details>
      )}
    </details>
  );
}
