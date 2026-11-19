"""Deterministic datum-to-study provenance; never calls the registry or an LLM."""

from collections.abc import Sequence

from app.aggregation import (
    PHASE_LABELS,
    annual_bucket,
    category_values_by_study,
    enrollment_bin,
    named_drugs,
    unique_studies,
)
from app.clinical_trials import RetrievalResult
from app.debug import debug
from app.errors import ServiceError
from app.models import CitedVisualizationResponse, QueryPlan, VisualizationResponse
from app.models.citations import (
    CitationBundle,
    DatumCitation,
    NodeCitation,
    SourceField,
    SourceValue,
    StudyCitation,
)
from app.models.enums import Grouping
from app.models.network import NetworkVisualization
from app.normalization import _year
from app.provenance import (
    NCT_PATH,
    CapturedStudy,
    bounded_size,
    canonical,
    inconsistent,
    limit_exceeded,
)


def _text(field: SourceField) -> str | None:
    value = field.value if isinstance(field, SourceValue) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _selected_fields(
    record: CapturedStudy, grouping: Grouping, category: str | int | tuple[str, str]
) -> list[SourceField]:
    identity = [f for f in record.fields if f.path == NCT_PATH]
    fields = [f for f in record.fields if f.path != NCT_PATH]
    if grouping == Grouping.PHASE and category != "Missing phase":
        fields = [
            f
            for f in fields
            if _text(f) is not None
            and PHASE_LABELS.get(_text(f), f"Unrecognized phase: {_text(f)}") == category
        ]
    elif grouping == Grouping.PHASE:
        expected = "/protocolSection/designModule/phases"
        if len(fields) != 1 or fields[0].path != expected:
            raise inconsistent()
        if (
            isinstance(fields[0], SourceValue)
            and isinstance(fields[0].value, list)
            and any(isinstance(value, str) and value.strip() for value in fields[0].value)
        ):
            raise inconsistent()
    elif grouping in (Grouping.STATUS, Grouping.SPONSOR_CLASS):
        missing = "Missing status" if grouping == Grouping.STATUS else "Missing lead sponsor class"
        if category != missing and (len(fields) != 1 or _text(fields[0]) != category):
            raise inconsistent()
        if category == missing and any(_text(f) for f in fields):
            raise inconsistent()
    elif grouping == Grouping.START_YEAR:
        if (
            len(fields) != 1
            or _year(_text(fields[0])) is None
            or f"{_year(_text(fields[0])):04d}-01-01" != category
        ):
            raise inconsistent()
    elif grouping == Grouping.ENROLLMENT:
        counts = [f for f in fields if f.path.endswith("/count")]
        if (
            len(counts) != 1
            or not isinstance(counts[0], SourceValue)
            or type(counts[0].value) is not int
            or enrollment_bin(counts[0].value) != category
        ):
            raise inconsistent()
    elif grouping == Grouping.COUNTRY and category != "Missing country":
        fields = [f for f in fields if _text(f) == category]
    elif grouping == Grouping.COUNTRY:
        if any(f.path.endswith("/country") and _text(f) is not None for f in fields):
            raise inconsistent()
    elif grouping == Grouping.INTERVENTION:
        fields = [f for f in fields if (_text(f) or "Missing intervention type") == category]
    elif grouping == Grouping.SPONSOR_DRUG:
        sponsor, drug = category
        sponsors = [
            f for f in fields if f.path.endswith("/leadSponsor/name") and _text(f) == sponsor
        ]
        names = [f for f in fields if f.path.endswith("/name") and _text(f) == drug]
        indices = {f.path.rsplit("/", 1)[0] for f in names}
        types = [
            f
            for f in fields
            if f.path.endswith("/type")
            and _text(f) == "DRUG"
            and f.path.rsplit("/", 1)[0] in indices
        ]
        names = [
            f
            for f in names
            if f.path.rsplit("/", 1)[0] in {t.path.rsplit("/", 1)[0] for t in types}
        ]
        fields = [*sponsors, *names, *types]
        if not sponsors or not names or not types:
            raise inconsistent()
    if len(identity) != 1 or not fields:
        raise inconsistent()
    return [*identity, *sorted(fields, key=lambda f: f.path)]


def _supports(
    response: VisualizationResponse,
    plan: QueryPlan,
    results: Sequence[RetrievalResult],
    maximum: int,
):
    """Count bounded distinct contributions before constructing any study citations."""
    chart = response.visualization
    graph = isinstance(chart, NetworkVisualization)
    rows = chart.data.edges if graph else chart.data
    ids = [set() for _ in rows]
    keys = {}
    categories = []
    if graph:
        labels = {node.id: node.label for node in chart.data.nodes}
        for index, edge in enumerate(rows):
            key = (labels[edge.source], labels[edge.target])
            keys[key] = index
            categories.append(key)
    else:
        for index, row in enumerate(rows):
            category = (
                index if plan.group_by == Grouping.ENROLLMENT else row[chart.encoding.x.field]
            )
            cohort = plan.comparison.values.index(row["cohort"]) if plan.comparison else 0
            keys[cohort, category] = index
            categories.append(category)
    references = 0
    for cohort, result in enumerate(results):
        membership = (
            category_values_by_study(result.studies, plan.group_by)
            if plan.group_by
            in {
                Grouping.PHASE,
                Grouping.STATUS,
                Grouping.SPONSOR_CLASS,
                Grouping.COUNTRY,
                Grouping.INTERVENTION,
            }
            else None
        )
        for study in unique_studies(result.studies):
            if graph:
                values = (
                    [(study.sponsor_name, drug) for drug in named_drugs(study)]
                    if study.sponsor_name
                    else []
                )
                indices = [keys[v] for v in values if v in keys]
            else:
                values = (
                    membership[study.nct_id]
                    if membership is not None
                    else [annual_bucket(study)]
                    if plan.group_by == Grouping.START_YEAR
                    else [enrollment_bin(study.enrollment_count)]
                )
                indices = [keys[cohort, v] for v in values if (cohort, v) in keys]
            for index in indices:
                if study.nct_id not in ids[index]:
                    references += 1
                    if references > maximum:
                        raise limit_exceeded()
                    ids[index].add(study.nct_id)
    return ids, categories, references


def build_cited_response(
    response: VisualizationResponse,
    plan: QueryPlan,
    results: Sequence[RetrievalResult],
    max_references: int,
    max_bytes: int,
) -> CitedVisualizationResponse:
    try:
        ids, categories, references = _supports(response, plan, results, max_references)
        graph = isinstance(response.visualization, NetworkVisualization)
        datums = []
        validated_records = set()
        records_by_cohort = [
            {s.nct_id: s for s in unique_studies(result.studies)} for result in results
        ]
        serialized_bytes = 0
        for index, members in enumerate(ids):
            cohort_label = response.visualization.data[index]["cohort"] if plan.comparison else None
            cohort = plan.comparison.values.index(cohort_label) if plan.comparison else 0
            result = results[cohort]
            records = records_by_cohort[cohort]
            studies = []
            for nct_id in sorted(members):
                if result.evidence is None or nct_id not in result.evidence:
                    raise inconsistent()
                captured = result.evidence[nct_id]
                if (cohort, nct_id) not in validated_records:
                    captured.validate(records[nct_id], plan.group_by)
                    validated_records.add((cohort, nct_id))
                fields = _selected_fields(captured, plan.group_by, categories[index])
                study = StudyCitation(
                    nct_id=nct_id,
                    study_url=f"https://clinicaltrials.gov/study/{nct_id}",
                    fields=fields,
                )
                serialized_bytes += bounded_size(
                    study.model_dump(mode="json"), max_bytes - serialized_bytes
                )
                studies.append(study)
            datums.append(
                DatumCitation(
                    datum_path=f"/visualization/data/{'edges/' if graph else ''}{index}",
                    cohort=cohort_label,
                    basis="source_fields" if members else "complete_cohort_zero",
                    supporting_study_count=len(members),
                    studies=studies,
                )
            )
        nodes = []
        if graph:
            for node in response.visualization.data.nodes:
                nodes.append(
                    NodeCitation(
                        node_id=node.id,
                        edge_paths=[
                            f"/visualization/data/edges/{i}"
                            for i, edge in enumerate(response.visualization.data.edges)
                            if node.id in (edge.source, edge.target)
                        ],
                    )
                )
        bundle = CitationBundle(reference_count=references, datums=datums, nodes=nodes)
        bounded_size(bundle.model_dump(mode="json"), max_bytes)
        cited = CitedVisualizationResponse(**response.model_dump(), citations=bundle)
        # Construction already checked source bindings and selected fields above;
        # the HTTP boundary repeats those checks against the final serialized JSON.
        debug("citations.complete", references=references, datums=len(datums))
        return cited
    except Exception as exc:
        if isinstance(exc, ServiceError):
            debug("citations.failed", category=exc.code)
            raise
        raise inconsistent() from None


def validate_evidence(
    response: CitedVisualizationResponse,
    plan: QueryPlan,
    results: Sequence[RetrievalResult],
    maximum: int,
) -> None:
    """Recheck source binding after serialization, including cohort and raw-value identity."""
    expected_ids, categories, _ = _supports(response, plan, results, maximum)
    validated_records = set()
    records_by_cohort = [
        {s.nct_id: s for s in unique_studies(result.studies)} for result in results
    ]
    for index, datum in enumerate(response.citations.datums):
        if {s.nct_id for s in datum.studies} != expected_ids[index]:
            raise inconsistent()
        cohort = plan.comparison.values.index(datum.cohort) if plan.comparison else 0
        result = results[cohort]
        records = records_by_cohort[cohort]
        for study in datum.studies:
            captured = (result.evidence or {}).get(study.nct_id)
            if captured is None or study.nct_id not in records:
                raise inconsistent()
            if (cohort, study.nct_id) not in validated_records:
                captured.validate(records[study.nct_id], plan.group_by)
                validated_records.add((cohort, study.nct_id))
            fields = _selected_fields(captured, plan.group_by, categories[index])
            if canonical([f.model_dump(mode="json") for f in fields]) != canonical(
                [f.model_dump(mode="json") for f in study.fields]
            ):
                raise inconsistent()
