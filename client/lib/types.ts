export const TRIAL_STATUSES = [
  "NOT_YET_RECRUITING",
  "RECRUITING",
  "ENROLLING_BY_INVITATION",
  "ACTIVE_NOT_RECRUITING",
  "SUSPENDED",
  "TERMINATED",
  "COMPLETED",
  "WITHDRAWN",
  "AVAILABLE",
  "NO_LONGER_AVAILABLE",
  "TEMPORARILY_NOT_AVAILABLE",
  "APPROVED_FOR_MARKETING",
  "WITHHELD",
  "UNKNOWN",
] as const;

export type TrialStatus = (typeof TRIAL_STATUSES)[number];

export type ChartType =
  "bar_chart" | "time_series" | "grouped_bar_chart" | "histogram" | "network_graph";

export interface StudyFilters {
  condition?: string | null;
  drug_name?: string | null;
  country?: string | null;
  sponsor?: string | null;
  trial_status?: TrialStatus | null;
  start_year?: number | null;
  end_year?: number | null;
}

export interface VisualizationRequest extends StudyFilters {
  query: string;
  preferred_chart?: ChartType | null;
  include_citations?: boolean;
}

export interface AxisEncoding {
  field: string;
  type: "nominal" | "ordinal" | "quantitative" | "temporal";
  title: string;
}

export type CartesianRow = Record<string, string | number | null>;

export interface CartesianVisualization {
  type: "bar_chart" | "time_series" | "grouped_bar_chart";
  title: string;
  encoding: { x: AxisEncoding; y: AxisEncoding; series?: AxisEncoding | null };
  data: CartesianRow[];
}

export interface EnrollmentBin {
  bin_label: string;
  bin_lower: number;
  bin_upper: number | null;
  trial_count: number;
}

export interface HistogramVisualization {
  type: "histogram";
  title: string;
  encoding: { x: AxisEncoding; x2: AxisEncoding; y: AxisEncoding };
  data: EnrollmentBin[];
}

export interface GraphNode {
  id: string;
  label: string;
  entity_type: "sponsor" | "drug";
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
}

export interface NetworkVisualization {
  type: "network_graph";
  title: string;
  encoding: Record<
    "node_id" | "node_label" | "node_type" | "source" | "target" | "weight",
    AxisEncoding
  >;
  data: { nodes: GraphNode[]; edges: GraphEdge[] };
}

export type Visualization = CartesianVisualization | HistogramVisualization | NetworkVisualization;

export interface CohortMeta {
  label: string;
  filters: StudyFilters;
  retrieved_studies: number;
  total_matches: number | null;
  complete_results: boolean;
  warnings?: string[];
}

export interface EnrollmentSummary {
  included_studies: number;
  excluded_studies: number;
  actual_studies: number;
  estimated_studies: number;
  unknown_type_studies: number;
}

export interface GraphSummary {
  included_studies: number;
  excluded_studies: number;
  missing_sponsor_studies: number;
  no_named_drug_studies: number;
  unnamed_drug_studies: number;
  total_nodes: number;
  total_edges: number;
  displayed_nodes: number;
  displayed_edges: number;
  omitted_nodes: number;
  omitted_edges: number;
  display_truncated: boolean;
  edge_limit: 200;
}

export interface VisualizationMeta {
  source: string;
  filters: StudyFilters;
  counting_unit: string;
  grouping: string;
  retrieved_studies: number;
  total_matches: number | null;
  complete_results: boolean;
  warnings?: string[];
  comparison_field?: "drug_name" | "condition" | null;
  cohorts?: CohortMeta[];
  overlap_studies?: number | null;
  histogram?: EnrollmentSummary | null;
  graph?: GraphSummary | null;
}

export type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export interface SourceValue {
  kind: "value" | "null" | "empty" | "invalid";
  path: string;
  value: JsonValue;
}

export interface ObjectKeys {
  kind: "object_keys";
  path: string;
  keys: string[];
}

export interface MissingField {
  kind: "absent";
  path: string;
  ancestor: ObjectKeys | SourceValue;
}

export type SourceField = SourceValue | MissingField;

export interface StudyCitation {
  nct_id: string;
  study_url: string;
  fields: SourceField[];
}

export interface DatumCitation {
  datum_path: string;
  cohort?: string | null;
  basis: "source_fields" | "complete_cohort_zero";
  supporting_study_count: number;
  studies: StudyCitation[];
}

export interface CitationBundle {
  source: "ClinicalTrials.gov Data API v2";
  reference_count: number;
  datums: DatumCitation[];
  nodes?: { node_id: string; edge_paths: string[] }[];
}

export interface VisualizationResponse {
  visualization: Visualization;
  meta: VisualizationMeta;
}

export interface CitedVisualizationResponse extends VisualizationResponse {
  citations: CitationBundle;
}

export type ResultResponse = VisualizationResponse & { citations?: CitationBundle };

export interface ErrorResponse {
  error: { code: string; message: string };
}
