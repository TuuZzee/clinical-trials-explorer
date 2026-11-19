"""Evidence is checked against raw records, not just fabricated citation objects."""

import asyncio
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from app.charts import build_chart
from app.citations import build_cited_response, validate_evidence
from app.clinical_trials import ClinicalTrialsClient
from app.config import Settings
from app.errors import ServiceError
from app.models import CitedVisualizationResponse, QueryPlan, StudyFilters, VisualizationRequest
from app.models.citations import MissingField, ObjectKeys
from app.models.enums import Grouping
from app.normalization import normalize_study
from app.provenance import EvidenceBudget, bounded_size, canonical


def raw(index=1):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": f"NCT{index:08d}"},
            "designModule": {
                "phases": ["PHASE2"],
                "enrollmentInfo": {"count": 30, "type": "ACTUAL"},
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "startDateStruct": {"date": "2020-04-23"},
            },
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": " Sponsor A ", "class": "NIH"}},
            "armsInterventionsModule": {
                "interventions": [
                    {"name": " Drug A ", "type": "DRUG"},
                    {"name": "Biological B", "type": "BIOLOGICAL"},
                ]
            },
            "contactsLocationsModule": {
                "locations": [{"country": " France "}, {"country": "Canada"}, {"country": "France"}]
            },
        }
    }


def plan(grouping="phase", comparison=False):
    return QueryPlan.model_validate(
        {
            "intent": "comparison"
            if comparison
            else "relationship"
            if grouping == "sponsor_drug"
            else "time_trend"
            if grouping == "start_year"
            else "geographic"
            if grouping == "country"
            else "distribution",
            "measure": "study_count",
            "filters": {},
            "group_by": grouping,
            "chart_type": "grouped_bar_chart"
            if comparison
            else "network_graph"
            if grouping == "sponsor_drug"
            else "histogram"
            if grouping == "enrollment_count"
            else "time_series"
            if grouping == "start_year"
            else "bar_chart",
            "comparison": {"field": "drug_name", "values": ["Drug A", "Drug B"]}
            if comparison
            else None,
        }
    )


def retrieve(records, grouping, budget=None):
    async def run():
        total = len(
            {r["protocolSection"]["identificationModule"]["nctId"].strip() for r in records}
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"studies": records, "totalCount": total})
            )
        ) as http:
            return await ClinicalTrialsClient(http, Settings(_env_file=None)).search(
                StudyFilters(),
                evidence_grouping=Grouping(grouping),
                evidence_budget=budget or EvidenceBudget(1_000_000),
            )

    return asyncio.run(run())


def cited(records, grouping="phase", *, max_refs=1000, max_bytes=1_000_000):
    p = plan(grouping)
    result = retrieve(records, grouping)
    ordinary = build_chart(p, [result])
    output = build_cited_response(ordinary, p, [result], max_refs, max_bytes)
    validated = CitedVisualizationResponse.model_validate_json(output.model_dump_json())
    validate_evidence(validated, p, [result], max_refs)
    assert output.model_dump(mode="json", exclude={"citations"}) == ordinary.model_dump(mode="json")
    check_sources(output, [records])
    return output


def read_pointer(source, path):
    for token in path.split("/")[1:] if path else []:
        token = token.replace("~1", "/").replace("~0", "~")
        source = source[int(token)] if isinstance(source, list) else source[token]
    return source


def check_sources(output, raw_cohorts):
    for datum in output.citations.datums:
        index = (
            [cohort.label for cohort in output.meta.cohorts].index(datum.cohort)
            if datum.cohort
            else 0
        )
        sources = {}
        for source in raw_cohorts[index]:
            sources.setdefault(
                source["protocolSection"]["identificationModule"]["nctId"].strip(), source
            )
        for study in datum.studies:
            source = sources[study.nct_id]
            assert study.study_url == f"https://clinicaltrials.gov/study/{study.nct_id}"
            for evidence in study.fields:
                if isinstance(evidence, MissingField):
                    with pytest.raises((KeyError, TypeError, IndexError, ValueError)):
                        read_pointer(source, evidence.path)
                    ancestor = read_pointer(source, evidence.ancestor.path)
                    if isinstance(evidence.ancestor, ObjectKeys):
                        assert sorted(ancestor) == evidence.ancestor.keys
                    else:
                        assert canonical(ancestor) == canonical(evidence.ancestor.value)
                else:
                    assert canonical(read_pointer(source, evidence.path)) == canonical(
                        evidence.value
                    )


@pytest.mark.parametrize(
    "grouping",
    [
        "phase",
        "overall_status",
        "sponsor_class",
        "country",
        "intervention",
        "start_year",
        "enrollment_count",
        "sponsor_drug",
    ],
)
def test_all_groupings_cite_exact_source_fields_and_preserve_chart(grouping, synthetic_study):
    output = cited([synthetic_study], grouping)
    assert output.citations.reference_count > 0


def test_phase_multiple_members_indices_whitespace_and_first_record_wins():
    first, second = raw(), raw(2)
    first["protocolSection"]["designModule"]["phases"] = [" PHASE2 ", "PHASE1", "PHASE2"]
    duplicate = deepcopy(first)
    duplicate["protocolSection"]["designModule"]["phases"] = ["PHASE4"]
    output = cited([first, second, duplicate])
    assert output.citations.reference_count == 3
    phase2 = output.citations.datums[1]
    assert phase2.supporting_study_count == 2
    fields = phase2.studies[0].fields
    assert [(f.path, f.value) for f in fields if f.path.endswith(("/0", "/2"))] == [
        ("/protocolSection/designModule/phases/0", " PHASE2 "),
        ("/protocolSection/designModule/phases/2", "PHASE2"),
    ]
    assert "Phase 4" not in [row["phase"] for row in output.visualization.data]


@pytest.mark.parametrize(
    "grouping,module,field",
    [
        ("phase", "designModule", "phases"),
        ("overall_status", "statusModule", "overallStatus"),
        ("sponsor_class", "sponsorCollaboratorsModule", "leadSponsor"),
        ("country", "contactsLocationsModule", "locations"),
        ("intervention", "armsInterventionsModule", "interventions"),
    ],
)
@pytest.mark.parametrize("state", ["absent", "null", "empty", "invalid"])
def test_missing_categories_have_genuine_absence_or_literal_evidence(
    grouping, module, field, state
):
    source = raw()
    parent = source["protocolSection"][module]
    if state == "absent":
        del parent[field]
    else:
        parent[field] = {"null": None, "empty": [], "invalid": 17}[state]
    output = cited([source], grouping)
    assert output.citations.datums[0].supporting_study_count == 1
    fields = output.citations.datums[0].studies[0].fields[1:]
    proof = fields[0]
    if state == "absent":
        assert isinstance(proof, MissingField)
        assert isinstance(proof.ancestor, ObjectKeys)
        assert field not in proof.ancestor.keys
    elif grouping == "sponsor_class":
        # The class is absent beneath a real null/empty/invalid leadSponsor.
        assert isinstance(proof, MissingField)
        assert proof.ancestor.kind == state
        assert canonical(proof.ancestor.value) == canonical(parent[field])
    else:
        assert proof.kind == state
        assert canonical(proof.value) == canonical(parent[field])


def test_missing_intervention_type_and_country_preserve_entry_positions():
    source = raw()
    source["protocolSection"]["armsInterventionsModule"]["interventions"] = [
        {"name": "A", "type": "DRUG"},
        {"name": "B"},
        {"name": "C", "type": None},
    ]
    output = cited([source], "intervention")
    missing = output.citations.datums[-1]
    assert missing.supporting_study_count == 1
    fields = missing.studies[0].fields[1:]
    assert fields[0].path.endswith("/1/type") and fields[0].kind == "absent"
    assert fields[1].path.endswith("/2/type") and fields[1].kind == "null"
    source["protocolSection"]["contactsLocationsModule"]["locations"] = [{}, {"country": None}]
    cited([source], "country")


@pytest.mark.parametrize(
    "grouping,value", [("phase", "NEW_PHASE"), ("overall_status", "NEW_STATUS")]
)
def test_unknown_reported_codes_remain_exact(grouping, value):
    source = raw()
    if grouping == "phase":
        source["protocolSection"]["designModule"]["phases"] = [value]
    else:
        source["protocolSection"]["statusModule"]["overallStatus"] = value
    output = cited([source], grouping)
    assert output.citations.datums[0].studies[0].fields[1].value == value


def test_annual_evidence_does_not_invent_a_january_start_date():
    output = cited([raw()], "start_year")
    assert output.visualization.data[0]["start_year"] == "2020-01-01"
    assert output.citations.datums[0].studies[0].fields[1].value == "2020-04-23"


@pytest.mark.parametrize(
    "count,index",
    [(0, 0), (9, 0), (10, 1), (49, 1), (50, 2), (100, 3), (500, 4), (1000, 5), (5000, 6)],
)
def test_histogram_boundaries_actual_estimated_and_zero_evidence(count, index):
    first, second = raw(), raw(2)
    first["protocolSection"]["designModule"]["enrollmentInfo"] = {"count": count, "type": "ACTUAL"}
    second["protocolSection"]["designModule"]["enrollmentInfo"] = {
        "count": count,
        "type": "ESTIMATED",
    }
    output = cited([first, second], "enrollment_count")
    assert output.citations.datums[index].supporting_study_count == 2
    assert output.citations.reference_count == 2
    for i, datum in enumerate(output.citations.datums):
        if i != index:
            assert datum.studies == [] and datum.basis == "complete_cohort_zero"


def test_overlapping_cohorts_and_aligned_zero_have_separate_source_binding():
    first, second = raw(), raw(2)
    first["protocolSection"]["designModule"]["phases"] = ["PHASE1"]
    p = plan(comparison=True)
    budget = EvidenceBudget(1_000_000)
    results = [retrieve([first, second], "phase", budget), retrieve([second], "phase", budget)]
    output = build_cited_response(build_chart(p, results), p, results, 1000, 1_000_000)
    check_sources(output, [[first, second], [second]])
    assert output.meta.overlap_studies == 1 and output.meta.retrieved_studies == 2
    assert output.citations.reference_count == 3
    assert [(d.cohort, d.supporting_study_count) for d in output.citations.datums] == [
        ("Drug A", 1),
        ("Drug B", 0),
        ("Drug A", 1),
        ("Drug B", 1),
    ]
    assert output.citations.datums[1].basis == "complete_cohort_zero"


def test_graph_edges_weights_multiple_drugs_and_connected_node_proof():
    first, second = raw(), raw(2)
    first["protocolSection"]["armsInterventionsModule"]["interventions"] += [
        {"name": " Drug A ", "type": "DRUG"},
        {"name": "Drug C", "type": " DRUG "},
    ]
    output = cited([first, second], "sponsor_drug")
    assert [edge.weight for edge in output.visualization.data.edges] == [2, 1]
    assert output.citations.reference_count == 3
    assert len(output.citations.nodes) == 3
    assert all(node.edge_paths for node in output.citations.nodes)


def test_display_limited_graph_cites_all_and_only_displayed_edges():
    source = raw()
    source["protocolSection"]["armsInterventionsModule"]["interventions"] = [
        {"name": f"Drug {i:03}", "type": "DRUG"} for i in range(205)
    ]
    output = cited([source], "sponsor_drug", max_refs=200)
    assert output.meta.graph.display_truncated and output.meta.graph.omitted_edges == 5
    assert output.citations.reference_count == len(output.citations.datums) == 200


@pytest.mark.parametrize("maximum,success", [(2, True), (1, False)])
def test_reference_limit_is_exact_and_never_samples(maximum, success):
    if success:
        assert cited([raw(), raw(2)], max_refs=maximum).citations.reference_count == maximum
    else:
        with pytest.raises(ServiceError) as exc:
            cited([raw(), raw(2)], max_refs=maximum)
        assert (exc.value.code, exc.value.status_code) == ("citation_limit_exceeded", 422)


def test_output_byte_limit_and_oversized_raw_evidence_fail_closed():
    output = cited([raw()])
    size = bounded_size(output.citations.model_dump(mode="json"), 1_000_000)
    assert cited([raw()], max_bytes=size).citations.reference_count == 1
    with pytest.raises(ServiceError) as exc:
        cited([raw()], max_bytes=size - 1)
    assert exc.value.code == "citation_limit_exceeded"
    source = raw()
    source["protocolSection"]["designModule"]["phases"] = ["X" * 2000]
    with pytest.raises(ServiceError, match="Citation limit exceeded"):
        retrieve([source], "phase", EvidenceBudget(200))


def test_tampered_source_and_wrong_source_values_are_not_valid_evidence():
    p = plan()
    result = retrieve([raw()], "phase")
    ordinary = build_chart(p, [result])
    output = build_cited_response(ordinary, p, [result], 1000, 1_000_000)
    altered = output.model_dump(mode="json")
    altered["citations"]["datums"][0]["studies"][0]["fields"][1]["value"] = "PHASE4"
    with pytest.raises(ServiceError, match="consistency"):
        validate_evidence(CitedVisualizationResponse.model_validate(altered), p, [result], 1000)
    result.evidence["NCT00000001"].fields[1].value = "PHASE4"
    with pytest.raises(ServiceError, match="consistency"):
        build_cited_response(ordinary, p, [result], 1000, 1_000_000)


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, [], {}])
def test_citation_flag_accepts_only_boolean(value):
    with pytest.raises(ValidationError):
        VisualizationRequest(query="Show phases", include_citations=value)


def test_absence_uses_actual_ancestor_keys_without_exporting_unrelated_values():
    source = raw()
    del source["protocolSection"]["designModule"]["phases"]
    source["protocolSection"]["designModule"]["unrelated"] = "not-an-excerpt" * 100_000
    output = cited([source], max_bytes=5000)
    proof = output.citations.datums[0].studies[0].fields[1]
    assert proof.kind == "absent"
    assert proof.ancestor.path == "/protocolSection/designModule"
    assert proof.ancestor.keys == ["enrollmentInfo", "unrelated"]
    assert "not-an-excerpt" not in output.model_dump_json()


@pytest.mark.parametrize(
    "grouping", ["phase", "overall_status", "sponsor_class", "country", "intervention"]
)
def test_missing_entire_module_uses_existing_protocol_ancestor(grouping):
    source = {"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}}
    output = cited([source], grouping)
    proof = output.citations.datums[0].studies[0].fields[1]
    assert proof.kind == "absent" and proof.ancestor.path == "/protocolSection"
    assert proof.ancestor.keys == ["identificationModule"]


@pytest.mark.parametrize("grouping", ["start_year", "enrollment_count", "sponsor_drug"])
def test_excluded_records_require_no_citation_capture_or_references(grouping):
    source = {"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}}
    result = retrieve([source], grouping, EvidenceBudget(1))
    assert result.evidence == {}
    p = plan(grouping)
    output = build_cited_response(build_chart(p, [result]), p, [result], 1, 1000)
    assert output.citations.reference_count == 0 and output.citations.datums == []


def test_wrong_field_path_or_duplicate_identity_fails_validation():
    p = plan()
    result = retrieve([raw()], "phase")
    output = build_cited_response(build_chart(p, [result]), p, [result], 1000, 1_000_000)
    altered = output.model_dump(mode="json")
    altered["citations"]["datums"][0]["studies"][0]["fields"][1]["path"] = (
        "/protocolSection/designModule/phases/8"
    )
    with pytest.raises(ServiceError):
        validate_evidence(CitedVisualizationResponse.model_validate(altered), p, [result], 1000)
    altered = output.model_dump(mode="json")
    datum = altered["citations"]["datums"][0]
    datum["studies"].append(deepcopy(datum["studies"][0]))
    with pytest.raises(ValidationError):
        CitedVisualizationResponse.model_validate(altered)


def test_missing_proof_cannot_claim_an_observed_key_is_absent():
    with pytest.raises(ValidationError):
        MissingField(
            path="/protocolSection/designModule/phases",
            ancestor=ObjectKeys(path="/protocolSection/designModule", keys=["phases"]),
        )


def test_missing_node_to_edge_proof_is_rejected():
    output = cited([raw()], "sponsor_drug").model_dump(mode="json")
    output["citations"]["nodes"] = []
    with pytest.raises(ValidationError):
        CitedVisualizationResponse.model_validate(output)


def test_raw_live_verifier_checks_actual_values_and_rejects_a_changed_record():
    from scripts.smoke_citations import verify_raw_phase_citations

    source = raw()
    output = cited([source])
    assert verify_raw_phase_citations(output, {"NCT00000001": source}) == 2
    changed = deepcopy(source)
    changed["protocolSection"]["designModule"]["phases"] = ["PHASE4"]
    with pytest.raises(ValueError):
        verify_raw_phase_citations(output, {"NCT00000001": changed})


def test_citation_smoke_tool_requires_explicit_live_opt_in(monkeypatch, capsys):
    from scripts import smoke_citations

    def forbidden(*args, **kwargs):
        pytest.fail("No live calls without --live")

    monkeypatch.setattr(smoke_citations.asyncio, "run", forbidden)
    assert smoke_citations.cli([]) == 0
    assert "--live" in capsys.readouterr().out


@pytest.mark.parametrize("grouping,attribute", [("phase", "phases"), ("country", "countries")])
def test_a_missing_category_cannot_cite_present_valid_source_members(grouping, attribute):
    from app.clinical_trials import RetrievalResult
    from app.provenance import capture_study

    source = raw()
    wrong = normalize_study(source).model_copy(update={attribute: ()})
    captured = capture_study(source, wrong, Grouping(grouping), EvidenceBudget(1_000_000))
    result = RetrievalResult(
        studies=[wrong],
        total_matches=1,
        retrieved_records=1,
        pages_fetched=1,
        evidence={wrong.nct_id: captured},
    )
    p = plan(grouping)
    with pytest.raises(ServiceError, match="consistency"):
        build_cited_response(build_chart(p, [result]), p, [result], 1000, 1_000_000)


@pytest.mark.parametrize("state", ["absent", "null", "empty"])
def test_nested_sponsor_class_absence_is_distinct_from_null_or_empty(state):
    source = raw()
    sponsor = source["protocolSection"]["sponsorCollaboratorsModule"]["leadSponsor"]
    if state == "absent":
        del sponsor["class"]
    else:
        sponsor["class"] = None if state == "null" else ""
    output = cited([source], "sponsor_class")
    field = output.citations.datums[0].studies[0].fields[1]
    assert field.path.endswith("/leadSponsor/class") and field.kind == state
    if state == "absent":
        assert field.ancestor.keys == ["name"]
    else:
        assert field.value == sponsor["class"]


@pytest.mark.parametrize(
    "grouping,module,field,value",
    [
        ("phase", "designModule", "phases", ["PHASE4"]),
        ("overall_status", "statusModule", "overallStatus", "COMPLETED"),
        ("sponsor_class", "sponsorCollaboratorsModule", "leadSponsor", {"class": "INDUSTRY"}),
        ("country", "contactsLocationsModule", "locations", [{"country": "Japan"}]),
        ("intervention", "armsInterventionsModule", "interventions", [{"type": "DEVICE"}]),
    ],
)
def test_direct_duplicate_category_union_cannot_borrow_uncaptured_evidence(
    grouping, module, field, value
):
    first = raw()
    duplicate = deepcopy(first)
    duplicate["protocolSection"][module][field] = value
    p = plan(grouping)
    # The production adapter supplies one normalized record and its matching evidence.
    result = retrieve([first, duplicate], grouping)
    assert len(result.studies) == 1
    ordinary = build_chart(p, [result])
    output = build_cited_response(ordinary, p, [result], 1000, 1_000_000)
    check_sources(output, [[first, duplicate]])
    # Direct categorical builders historically union conflicting duplicate records.
    # Keep that contract, but never cite later values absent from the captured first record.
    direct = result.model_copy(
        update={"studies": [normalize_study(first), normalize_study(duplicate)]}
    )
    union = build_chart(p, [direct])
    assert len(union.visualization.data) > len(ordinary.visualization.data)
    assert all(row["trial_count"] == 1 for row in union.visualization.data)
    with pytest.raises(ServiceError) as exc:
        build_cited_response(union, p, [direct], 1000, 1_000_000)
    assert exc.value.code == "invalid_citations"
