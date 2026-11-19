import type { VisualizationRequest } from "./types";

// Example questions with filters that bound live retrieval.
export const PRESETS: { id: string; label: string; request: VisualizationRequest }[] = [
  {
    id: "phase_distribution",
    label: "Phase distribution",
    request: {
      query: "Show the phase distribution of breast cancer trials involving pembrolizumab",
      sponsor: "National Cancer Institute",
      country: "United States",
      trial_status: "COMPLETED",
      start_year: 2015,
      end_year: 2025,
    },
  },
  {
    id: "annual_trends",
    label: "Annual trends",
    request: {
      query: "How many breast cancer trials involving pembrolizumab started each year?",
      sponsor: "National Cancer Institute",
      country: "United States",
      trial_status: "COMPLETED",
      start_year: 2015,
      end_year: 2025,
    },
  },
  {
    id: "comparison_overlap",
    label: "Compare two drugs",
    request: {
      query: "Compare phases of lung cancer trials involving nivolumab versus ipilimumab",
      sponsor: "National Cancer Institute",
      country: "United States",
      trial_status: "COMPLETED",
      start_year: 2015,
      end_year: 2025,
    },
  },
  {
    id: "enrollment_recruiting",
    label: "Enrollment sizes",
    request: {
      query:
        "Show a histogram of enrollment sizes for recruiting breast cancer trials involving pembrolizumab",
      sponsor: "National Cancer Institute",
      country: "United States",
      trial_status: "RECRUITING",
      start_year: 2015,
      end_year: 2025,
    },
  },
  {
    id: "network_recruiting",
    label: "Sponsor–drug network",
    request: {
      query:
        "Show a network of lead sponsors and drugs for recruiting breast cancer trials involving pembrolizumab",
      sponsor: "National Cancer Institute",
      country: "United States",
      trial_status: "RECRUITING",
      start_year: 2015,
      end_year: 2025,
    },
  },
];
