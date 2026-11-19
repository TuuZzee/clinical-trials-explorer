"""Defensive normalization of ClinicalTrials.gov study records."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.errors import ServiceError


class Intervention(BaseModel):
    name: str | None
    type: str | None


class NormalizedStudy(BaseModel):
    nct_id: str
    overall_status: str | None = None
    start_date: str | None = None
    start_year: int | None = None
    phases: tuple[str, ...] = ()
    sponsor_name: str | None = None
    sponsor_class: str | None = None
    interventions: tuple[Intervention, ...] = ()
    countries: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    enrollment_count: int | None = Field(default=None, strict=True, ge=0)
    enrollment_type: str | None = None


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(sorted({text for item in _list(value) if (text := _text(item))}))


def _year(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        if len(value) == 4 and value.isdigit():
            return date(int(value), 1, 1).year
        if len(value) == 7:
            return date.fromisoformat(value + "-01").year
        if len(value) == 10:
            return date.fromisoformat(value).year
    except ValueError:
        pass
    return None


def normalize_study(raw: dict) -> NormalizedStudy:
    protocol = _object(raw.get("protocolSection"))
    nct_id = _text(_object(protocol.get("identificationModule")).get("nctId"))
    if (
        nct_id is None
        or len(nct_id) != 11
        or not nct_id.startswith("NCT")
        or not nct_id[3:].isascii()
        or not nct_id[3:].isdigit()
    ):
        raise ServiceError("upstream_invalid_response", "Study has a missing or invalid NCT ID.")
    status = _object(protocol.get("statusModule"))
    start_date = _text(_object(status.get("startDateStruct")).get("date"))
    sponsor = _object(_object(protocol.get("sponsorCollaboratorsModule")).get("leadSponsor"))
    interventions = _list(_object(protocol.get("armsInterventionsModule")).get("interventions"))
    locations = _list(_object(protocol.get("contactsLocationsModule")).get("locations"))
    enrollment = _object(_object(protocol.get("designModule")).get("enrollmentInfo"))
    count = enrollment.get("count")
    valid_count = count if type(count) is int and count >= 0 else None
    return NormalizedStudy(
        enrollment_count=valid_count,
        enrollment_type=_text(enrollment.get("type")),
        nct_id=nct_id,
        overall_status=_text(status.get("overallStatus")),
        start_date=start_date,
        start_year=_year(start_date),
        phases=_strings(_object(protocol.get("designModule")).get("phases")),
        sponsor_name=_text(sponsor.get("name")),
        sponsor_class=_text(sponsor.get("class")),
        interventions=tuple(
            Intervention(name=_text(i.get("name")), type=_text(i.get("type")))
            for i in interventions
            if isinstance(i, dict)
        ),
        countries=_strings([_object(location).get("country") for location in locations]),
        conditions=_strings(_object(protocol.get("conditionsModule")).get("conditions")),
    )
