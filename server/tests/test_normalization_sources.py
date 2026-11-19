"""Source-field checks: one documented real projection, plus explicit synthetic variants."""

from copy import deepcopy

import pytest

from app.normalization import normalize_study


@pytest.mark.parametrize(
    "path,attribute,expected",
    [
        (("identificationModule", "nctId"), "nct_id", "NCT99990001"),
        (("statusModule", "overallStatus"), "overall_status", "ACTIVE_NOT_RECRUITING"),
        (("statusModule", "startDateStruct", "date"), "start_date", "2022-08-17"),
        (
            ("sponsorCollaboratorsModule", "leadSponsor", "name"),
            "sponsor_name",
            "Synthetic Research Institute",
        ),
        (("sponsorCollaboratorsModule", "leadSponsor", "class"), "sponsor_class", "INDUSTRY"),
        (("designModule", "enrollmentInfo", "count"), "enrollment_count", 137),
        (("designModule", "enrollmentInfo", "type"), "enrollment_type", "ACTUAL"),
    ],
)
def test_synthetic_scalar_fields_match_raw_source(synthetic_study, path, attribute, expected):
    source = synthetic_study["protocolSection"]
    for key in path:
        source = source[key]
    assert source == expected  # Fixed values from the synthetic fixture.
    assert getattr(normalize_study(synthetic_study), attribute) == source


def test_synthetic_collections_preserve_source_membership_and_intervention_types(synthetic_study):
    source = synthetic_study["protocolSection"]
    normalized = normalize_study(synthetic_study)
    assert set(normalized.phases) == set(source["designModule"]["phases"]) == {"PHASE2"}
    raw_interventions = source["armsInterventionsModule"]["interventions"]
    assert [(item.name, item.type) for item in normalized.interventions] == [
        (item["name"], item["type"]) for item in raw_interventions
    ]
    types = {item.name: item.type for item in normalized.interventions}
    assert types["Synthetic Agent C"] == "BIOLOGICAL"  # No drug-type inference from the name.
    assert types["Synthetic Compound A"] == "DRUG"
    raw_countries = [
        location["country"] for location in source["contactsLocationsModule"]["locations"]
    ]
    assert len(raw_countries) == 3 and len(normalized.countries) == 1
    assert set(normalized.countries) == set(raw_countries) == {"Canada"}
    assert set(normalized.conditions) == set(source["conditionsModule"]["conditions"])


def test_start_year_comes_from_start_date_not_other_registry_dates(synthetic_study):
    raw = deepcopy(synthetic_study)
    status = raw["protocolSection"]["statusModule"]
    status.update(
        studyFirstSubmitDate="2015-10-01",
        primaryCompletionDateStruct={"date": "2019-03-01"},
        completionDateStruct={"date": "2021-03-01"},
    )
    assert normalize_study(raw).start_year == 2022
    del status["startDateStruct"]
    normalized = normalize_study(raw)
    assert normalized.start_date is None and normalized.start_year is None


def test_synthetic_multivalued_fields_are_trimmed_without_inventing_members():
    raw = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001"},
            "statusModule": {"overallStatus": "UNKNOWN", "startDateStruct": {"date": "2020-02"}},
            "designModule": {
                "phases": ["PHASE2", " PHASE1 ", "PHASE2"],
                "enrollmentInfo": {"count": 0, "type": "ESTIMATED"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": " Lead Sponsor ", "class": "INDUSTRY"},
                "collaborators": [{"name": "Collaborator", "class": "NIH"}],
            },
            "armsInterventionsModule": {
                "interventions": [
                    {"name": " Named biological ", "type": "BIOLOGICAL"},
                    {"name": "Named drug", "type": "DRUG"},
                    {"name": "Unclassified intervention"},
                ]
            },
            "contactsLocationsModule": {
                "locations": [
                    {"country": " France "},
                    {"country": "Canada"},
                    {"country": "France"},
                    {},
                ]
            },
        }
    }
    normalized = normalize_study(raw)
    assert normalized.phases == ("PHASE1", "PHASE2")
    assert normalized.countries == ("Canada", "France")
    assert normalized.overall_status == "UNKNOWN"
    assert (normalized.start_date, normalized.start_year) == ("2020-02", 2020)
    assert (normalized.sponsor_name, normalized.sponsor_class) == ("Lead Sponsor", "INDUSTRY")
    assert [(item.name, item.type) for item in normalized.interventions] == [
        ("Named biological", "BIOLOGICAL"),
        ("Named drug", "DRUG"),
        ("Unclassified intervention", None),
    ]
    assert (normalized.enrollment_count, normalized.enrollment_type) == (0, "ESTIMATED")
    assert normalized.conditions == ()


def test_synthetic_absent_fields_do_not_fall_back_to_unrelated_source_values():
    raw = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001"},
            "statusModule": {
                "lastKnownStatus": "RECRUITING",
                "completionDateStruct": {"date": "2025"},
            },
            "sponsorCollaboratorsModule": {"collaborators": [{"name": "Other", "class": "NIH"}]},
            "designModule": {"studyType": "INTERVENTIONAL"},
            "armsInterventionsModule": {"armGroups": [{"interventionNames": ["Drug: Example"]}]},
            "contactsLocationsModule": {"centralContacts": [{"name": "Country-like label"}]},
        }
    }
    assert normalize_study(raw).model_dump() == {
        "nct_id": "NCT00000001",
        "overall_status": None,
        "start_date": None,
        "start_year": None,
        "phases": (),
        "sponsor_name": None,
        "sponsor_class": None,
        "interventions": (),
        "countries": (),
        "conditions": (),
        "enrollment_count": None,
        "enrollment_type": None,
    }
