"""Bounded, request-local source evidence captured before registry records are discarded."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field

from app.errors import ServiceError
from app.models.citations import MissingField, SourceField, SourceValue
from app.models.enums import Grouping
from app.normalization import NormalizedStudy

NCT_PATH = "/protocolSection/identificationModule/nctId"
BASE = "/protocolSection"


def limit_exceeded() -> ServiceError:
    return ServiceError(
        "citation_limit_exceeded",
        "Citation limit exceeded. Narrow the filters or disable citations.",
        422,
    )


def inconsistent() -> ServiceError:
    return ServiceError(
        "invalid_citations", "Citation evidence failed consistency validation.", 500
    )


def canonical(value) -> str:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def bounded_size(value, maximum: int) -> int:
    # Reject enormous strings before the encoder creates an escaped copy.
    minimum = 0

    def check(item):
        nonlocal minimum
        minimum += 1 + (len(item) if isinstance(item, str) else 0)
        if minimum > maximum:
            raise limit_exceeded()
        if isinstance(item, dict):
            for key, child in item.items():
                check(key)
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)

    check(value)
    size = 0
    for chunk in json.JSONEncoder(
        ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > maximum:
            raise limit_exceeded()
    return size


@dataclass
class EvidenceBudget:
    max_bytes: int
    captured_bytes: int = 0

    def charge(self, value) -> None:
        self.captured_bytes += bounded_size(value, self.max_bytes - self.captured_bytes)


def pointer_parts(path: str) -> list[str]:
    if not path:
        return []
    if not path.startswith("/"):
        raise ValueError("Invalid JSON Pointer")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def resolve(raw, path: str):
    current = raw
    for part in pointer_parts(path):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list) and part.isdecimal():
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current


def source_field(raw: dict, path: str, budget: EvidenceBudget, *, unusable=False) -> SourceField:
    current = raw
    traversed = []
    for part in pointer_parts(path):
        parent_path = "".join(
            "/" + token.replace("~", "~0").replace("/", "~1") for token in traversed
        )
        try:
            if isinstance(current, dict):
                child = current[part]
            elif isinstance(current, list) and part.isdecimal():
                child = current[int(part)]
            else:
                raise KeyError(part)
        except (KeyError, IndexError):
            if isinstance(current, dict):
                keys = []
                minimum = 0
                for key in current:
                    minimum += len(key) + 1
                    if minimum > budget.max_bytes - budget.captured_bytes:
                        raise limit_exceeded() from None
                    keys.append(key)
                ancestor = {"kind": "object_keys", "path": parent_path, "keys": sorted(keys)}
            else:
                kind = (
                    "null" if current is None else "empty" if current in ("", [], {}) else "invalid"
                )
                ancestor = {"kind": kind, "path": parent_path, "value": current}
            payload = {"kind": "absent", "path": path, "ancestor": ancestor}
            budget.charge(payload)
            return MissingField.model_validate(deepcopy(payload))
        current = child
        traversed.append(part)
    kind = (
        "null"
        if current is None
        else "empty"
        if current in ("", [], {})
        else "invalid"
        if unusable
        else "value"
    )
    payload = {"kind": kind, "path": path, "value": current}
    budget.charge(payload)
    evidence = SourceValue.model_validate(deepcopy(payload))
    if canonical(evidence.value) != canonical(current):
        raise inconsistent()
    return evidence


def fingerprint(study: NormalizedStudy, grouping: Grouping) -> str:
    attributes = {
        Grouping.PHASE: ("phases",),
        Grouping.STATUS: ("overall_status",),
        Grouping.SPONSOR_CLASS: ("sponsor_class",),
        Grouping.COUNTRY: ("countries",),
        Grouping.START_YEAR: ("start_date", "start_year"),
        Grouping.ENROLLMENT: ("enrollment_count", "enrollment_type"),
    }
    if grouping in (Grouping.INTERVENTION, Grouping.SPONSOR_DRUG):
        values = tuple((i.name, i.type) for i in study.interventions)
        if grouping == Grouping.SPONSOR_DRUG:
            values = (study.sponsor_name, values)
    else:
        values = tuple(getattr(study, name) for name in attributes[grouping])
    return hashlib.sha256(canonical((study.nct_id, values)).encode()).hexdigest()


@dataclass
class CapturedStudy:
    """Bind selected raw excerpts to normalized inputs; detect accidental mutation.

    These internal hashes are consistency guards, not signed registry attestations.
    """

    grouping: Grouping
    normalized_hash: str
    fields: tuple[SourceField, ...]
    seal: str = field(init=False)

    def __post_init__(self):
        self.seal = self.digest()

    def digest(self) -> str:
        return hashlib.sha256(
            canonical(
                (
                    self.grouping.value,
                    self.normalized_hash,
                    [f.model_dump(mode="json") for f in self.fields],
                )
            ).encode()
        ).hexdigest()

    def validate(self, study: NormalizedStudy, grouping: Grouping) -> None:
        if (
            grouping != self.grouping
            or self.normalized_hash != fingerprint(study, grouping)
            or self.seal != self.digest()
        ):
            raise inconsistent()


def capture_study(
    raw: dict, study: NormalizedStudy, grouping: Grouping, budget: EvidenceBudget
) -> CapturedStudy | None:
    """Select source paths using original array indices, never normalized array order."""
    if (
        (grouping == Grouping.ENROLLMENT and study.enrollment_count is None)
        or (grouping == Grouping.START_YEAR and study.start_year is None)
        or (
            grouping == Grouping.SPONSOR_DRUG
            and (
                not study.sponsor_name
                or not any(i.type == "DRUG" and i.name for i in study.interventions)
            )
        )
    ):
        return None
    fields = [source_field(raw, NCT_PATH, budget)]

    def add(path, unusable=False):
        fields.append(source_field(raw, path, budget, unusable=unusable))

    if grouping == Grouping.PHASE:
        path = BASE + "/designModule/phases"
        if study.phases:
            for index, value in enumerate(resolve(raw, path)):
                if isinstance(value, str) and value.strip():
                    add(f"{path}/{index}")
        else:
            add(path, True)
    elif grouping in (Grouping.STATUS, Grouping.SPONSOR_CLASS, Grouping.START_YEAR):
        path, value = {
            Grouping.STATUS: (BASE + "/statusModule/overallStatus", study.overall_status),
            Grouping.SPONSOR_CLASS: (
                BASE + "/sponsorCollaboratorsModule/leadSponsor/class",
                study.sponsor_class,
            ),
            Grouping.START_YEAR: (BASE + "/statusModule/startDateStruct/date", study.start_year),
        }[grouping]
        add(path, value is None)
    elif grouping == Grouping.ENROLLMENT:
        add(BASE + "/designModule/enrollmentInfo/count")
        add(BASE + "/designModule/enrollmentInfo/type")
    elif grouping in (Grouping.INTERVENTION, Grouping.SPONSOR_DRUG):
        path = BASE + "/armsInterventionsModule/interventions"
        try:
            entries = resolve(raw, path)
        except (KeyError, IndexError):
            entries = None
        if grouping == Grouping.SPONSOR_DRUG:
            add(BASE + "/sponsorCollaboratorsModule/leadSponsor/name")
        if not isinstance(entries, list) or not any(isinstance(item, dict) for item in entries):
            add(path, True)
        else:
            for index, item in enumerate(entries):
                if not isinstance(item, dict):
                    continue
                if grouping == Grouping.SPONSOR_DRUG:
                    if (
                        str(item.get("type", "")).strip() != "DRUG"
                        or not isinstance(item.get("name"), str)
                        or not item["name"].strip()
                    ):
                        continue
                    add(f"{path}/{index}/name")
                add(
                    f"{path}/{index}/type",
                    not isinstance(item.get("type"), str) or not item["type"].strip(),
                )
    elif grouping == Grouping.COUNTRY:
        path = BASE + "/contactsLocationsModule/locations"
        try:
            locations = resolve(raw, path)
        except (KeyError, IndexError):
            locations = None
        if not isinstance(locations, list) or not locations:
            add(path, True)
        else:
            for index, location in enumerate(locations):
                value = location.get("country") if isinstance(location, dict) else None
                add(f"{path}/{index}/country", not isinstance(value, str) or not value.strip())
    return CapturedStudy(grouping, fingerprint(study, grouping), tuple(fields))
