"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { visualize } from "@/lib/api";
import { PRESETS } from "@/lib/presets";
import { TRIAL_STATUSES, type ResultResponse, type VisualizationRequest } from "@/lib/types";
import ResultCard from "../results/result-card";

const TEXT_FILTERS = ["condition", "drug_name", "country", "sponsor"] as const;
const EMPTY_FILTERS = {
  condition: "",
  drug_name: "",
  country: "",
  sponsor: "",
  trial_status: "",
  start_year: "",
  end_year: "",
};

type Filters = typeof EMPTY_FILTERS;

type Entry = {
  id: string;
  question: string;
  state: "loading" | "success" | "error";
  result?: ResultResponse;
  error?: string;
};

function requestFor(question: string, filters: Filters, citations: boolean): VisualizationRequest {
  const request: VisualizationRequest = { query: question.trim(), include_citations: citations };

  if (!request.query || request.query.length > 2000)
    throw new Error("Enter a question of 1–2000 characters.");

  for (const key of TEXT_FILTERS) {
    const value = filters[key].trim();

    if (!value) continue;

    if (value.length > 200 || /["\\[\]{}\x00-\x1f\x7f]/.test(value))
      throw new Error(
        "Filters must be literal phrases up to 200 characters, without quotes, brackets, backslashes or control characters.",
      );
    request[key] = value;
  }

  if (filters.trial_status)
    request.trial_status = filters.trial_status as VisualizationRequest["trial_status"];

  for (const key of ["start_year", "end_year"] as const) {
    if (!filters[key]) continue;

    if (!/^\d{4}$/.test(filters[key]) || +filters[key] < 1800 || +filters[key] > 2100)
      throw new Error("Year bounds must be integers between 1800 and 2100.");
    request[key] = Number(filters[key]);
  }

  if (
    request.start_year != null &&
    request.end_year != null &&
    request.start_year > request.end_year
  )
    throw new Error("Start year must not exceed end year.");

  return request;
}

export default function Explorer() {
  const [question, setQuestion] = useState("");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [citations, setCitations] = useState(false);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const pending = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => () => pending.current?.abort(), []);

  useEffect(() => {
    if (!busy && entries.length) input.current?.focus({ preventScroll: true });
  }, [busy, entries.length]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "auto" });
  }, [entries]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (pending.current) return;
    let request: VisualizationRequest;

    try {
      request = requestFor(question, filters, citations);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Check the request fields.");

      return;
    }

    setFormError("");
    const controller = new AbortController();

    pending.current = controller;
    setBusy(true);
    const id = crypto.randomUUID();

    setEntries((previous) => [...previous, { id, question: request.query, state: "loading" }]);

    try {
      const result = await visualize(request, controller.signal);

      if (!controller.signal.aborted) {
        setEntries((previous) =>
          previous.map((e) => (e.id === id ? { ...e, state: "success", result } : e)),
        );
        setQuestion("");
      }
    } catch (error) {
      if (!controller.signal.aborted)
        setEntries((previous) =>
          previous.map((e) =>
            e.id === id
              ? {
                  ...e,
                  state: "error",
                  error:
                    error instanceof TypeError
                      ? "Cannot connect to the service. Check the connection and retry."
                      : error instanceof Error
                        ? error.message
                        : "The request failed. Please retry.",
                }
              : e,
          ),
        );
    } finally {
      pending.current = null;

      if (!controller.signal.aborted) {
        setBusy(false);
      }
    }
  }

  function preset(index: number) {
    const request = PRESETS[index].request;

    setQuestion(request.query);
    setFilters(
      Object.fromEntries(
        Object.keys(EMPTY_FILTERS).map((key) => [
          key,
          String(request[key as keyof VisualizationRequest] ?? ""),
        ]),
      ) as Filters,
    );
    setFormError("");
    input.current?.focus();
  }

  const activeFilters = Object.values(filters).filter(Boolean).length;

  return (
    <main className="shell">
      <header className="app-header">
        <div className="brand-mark" aria-hidden="true">
          ⌁
        </div>
        <div>
          <h1>Clinical Trials Explorer</h1>
          <p>Ask questions about registered clinical trials.</p>
        </div>
        <span className="session-label">Session only</span>
      </header>
      <div className="independence">
        Each question is interpreted independently. Previous messages are shown for this session
        only.
      </div>
      <div className="history" aria-label="Question history">
        {!entries.length && (
          <section className="welcome">
            <span className="eyebrow">FROM QUESTION TO EVIDENCE</span>
            <h2>
              Explore the registry.
              <br />
              <span>See the studies behind the chart.</span>
            </h2>
            <p>
              Compare trial populations, follow annual trends, or explore reported enrollment and
              sponsor–drug relationships.
            </p>
          </section>
        )}
        <nav className="starters" aria-label="Example questions">
          <span>Try an example</span>
          <div>
            {PRESETS.map((p, i) => (
              <button key={p.id} disabled={busy} onClick={() => preset(i)}>
                {p.label} <span aria-hidden="true">↗</span>
              </button>
            ))}
          </div>
          <small>
            Examples fill the question and narrowing filters. Submit to query the live registry.
          </small>
        </nav>
        {entries.map((entry) => (
          <article key={entry.id} className="exchange">
            <p className="user-question">
              <span className="sr-only">Question: </span>
              {entry.question}
            </p>
            {entry.state === "loading" && (
              <div className="loading" role="status">
                <span className="spinner" /> Interpreting question and retrieving registry studies…
                <small>This can take up to two minutes.</small>
              </div>
            )}
            {entry.state === "error" && (
              <div className="error-panel" role="alert">
                <strong>Unable to complete this request</strong>
                <p>{entry.error}</p>
                <small>Your question remains in the composer so you can adjust and retry.</small>
              </div>
            )}
            {entry.result && <ResultCard result={entry.result} />}
          </article>
        ))}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={submit} aria-label="Ask a clinical-trial question">
        <fieldset disabled={busy}>
          <details className="filter-panel">
            <summary>
              Filters{activeFilters ? ` · ${activeFilters} applied` : " · optional"}
            </summary>
            <p>
              Explicit filters override the question’s extracted filters. They apply to your next
              request; no earlier question is sent.
            </p>
            <div className="filter-grid">
              {TEXT_FILTERS.map((key) => (
                <label key={key}>
                  {key === "drug_name"
                    ? "Drug / intervention"
                    : key === "sponsor"
                      ? "Sponsor / collaborator"
                      : key[0].toUpperCase() + key.slice(1)}
                  <input
                    name={key}
                    maxLength={200}
                    value={filters[key]}
                    onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}
                  />
                </label>
              ))}
              <label>
                Status
                <select
                  aria-label="Status"
                  value={filters.trial_status}
                  onChange={(e) => setFilters({ ...filters, trial_status: e.target.value })}
                >
                  <option value="">Any status</option>
                  {TRIAL_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
              {(["start_year", "end_year"] as const).map((key) => (
                <label key={key}>
                  {key === "start_year" ? "Study start: from year" : "Study start: through year"}
                  <input
                    name={key}
                    type="number"
                    min={1800}
                    max={2100}
                    step={1}
                    value={filters[key]}
                    onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}
                  />
                </label>
              ))}
            </div>
            <button type="button" className="text-button" onClick={() => setFilters(EMPTY_FILTERS)}>
              Clear filters
            </button>
          </details>
          <label className="sr-only" htmlFor="question">
            Your clinical-trial question
          </label>
          <textarea
            id="question"
            ref={input}
            rows={2}
            maxLength={2000}
            value={question}
            placeholder="Ask a question about clinical trials…"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
          />
          <div className="composer-actions">
            <label className="citation-toggle">
              <input
                type="checkbox"
                checked={citations}
                onChange={(e) => setCitations(e.target.checked)}
              />{" "}
              Include source citations
            </label>
            <button
              className="submit"
              type="submit"
              disabled={!question.trim() || busy}
              aria-label={busy ? "Request in progress" : "Submit question"}
            >
              {busy ? <span className="spinner" /> : <span aria-hidden="true">↑</span>}
            </button>
          </div>
        </fieldset>
        {formError && (
          <p className="form-error" role="alert">
            {formError}
          </p>
        )}
        <p className="composer-hint">
          Enter to send · Shift+Enter for a new line · Registry study counts
        </p>
      </form>
    </main>
  );
}
