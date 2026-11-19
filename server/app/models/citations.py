"""Exact source evidence and datum identities, separate from visualization rows."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StringConstraints, model_validator

from .base import NonNegativeInt, StrictModel

Pointer = Annotated[str, StringConstraints(pattern=r"^(?:/(?:[^~/]|~[01])*)+$")]
NctId = Annotated[str, StringConstraints(pattern=r"^NCT[0-9]{8}$")]


class SourceValue(StrictModel):
    kind: Literal["value", "null", "empty", "invalid"]
    path: Pointer
    value: JsonValue

    @model_validator(mode="after")
    def value_kind(self) -> Self:
        if self.kind == "null" and self.value is not None:
            raise ValueError("Null evidence must contain an actual null")
        if self.kind == "empty" and self.value not in ("", [], {}):
            raise ValueError("Empty evidence must contain an actual empty value")
        if self.kind == "value" and self.value is None:
            raise ValueError("Use null evidence for an explicit null")
        return self


class ObjectKeys(StrictModel):
    kind: Literal["object_keys"] = "object_keys"
    path: Annotated[str, StringConstraints(pattern=r"^(?:/(?:[^~/]|~[01])*)*$")]
    keys: list[str]

    @model_validator(mode="after")
    def unique_keys(self) -> Self:
        if self.keys != sorted(set(self.keys)):
            raise ValueError("Ancestor keys must be distinct and ordered")
        return self


class MissingField(StrictModel):
    kind: Literal["absent"] = "absent"
    path: Pointer
    ancestor: Annotated[ObjectKeys | SourceValue, Field(discriminator="kind")]

    @model_validator(mode="after")
    def absent_child(self) -> Self:
        ancestor = self.ancestor
        if not self.path.startswith(ancestor.path + "/"):
            raise ValueError("Missing evidence must name a real ancestor of its expected path")
        child = (
            self.path[len(ancestor.path) + 1 :].split("/")[0].replace("~1", "/").replace("~0", "~")
        )
        if isinstance(ancestor, ObjectKeys):
            if child in ancestor.keys:
                raise ValueError("An absent child cannot occur in the observed ancestor keys")
        elif isinstance(ancestor.value, dict) or (
            isinstance(ancestor.value, list)
            and child.isdecimal()
            and int(child) < len(ancestor.value)
        ):
            raise ValueError("Absence witness must show the missing or blocked child")
        return self


SourceField = Annotated[SourceValue | MissingField, Field(discriminator="kind")]


class StudyCitation(StrictModel):
    nct_id: NctId
    study_url: str
    fields: Annotated[list[SourceField], Field(min_length=2)]

    @model_validator(mode="after")
    def canonical_url(self) -> Self:
        if self.study_url != f"https://clinicaltrials.gov/study/{self.nct_id}":
            raise ValueError("Study URL must be canonical for its NCT ID")
        identity = [
            field
            for field in self.fields
            if field.path == "/protocolSection/identificationModule/nctId"
        ]
        if (
            len(identity) != 1
            or not isinstance(identity[0], SourceValue)
            or not isinstance(identity[0].value, str)
            or identity[0].value.strip() != self.nct_id
        ):
            raise ValueError("Each contribution must include its exact source NCT identity")
        paths = [field.path for field in self.fields]
        if len(set(paths)) != len(paths):
            raise ValueError("Source paths must be unique within a study contribution")
        return self


class DatumCitation(StrictModel):
    datum_path: Pointer
    cohort: str | None = None
    basis: Literal["source_fields", "complete_cohort_zero"]
    supporting_study_count: NonNegativeInt
    studies: list[StudyCitation]

    @model_validator(mode="after")
    def distinct_support(self) -> Self:
        ids = [study.nct_id for study in self.studies]
        if len(set(ids)) != len(ids) or ids != sorted(ids):
            raise ValueError("Supporting NCT IDs must be distinct and ordered")
        if self.supporting_study_count != len(ids):
            raise ValueError("Supporting study count must equal distinct evidence records")
        if (self.basis == "complete_cohort_zero") != (not ids):
            raise ValueError("Zero evidence must have no supporting records")
        return self


class NodeCitation(StrictModel):
    node_id: str
    edge_paths: Annotated[list[Pointer], Field(min_length=1)]


class CitationBundle(StrictModel):
    source: Literal["ClinicalTrials.gov Data API v2"] = "ClinicalTrials.gov Data API v2"
    reference_count: NonNegativeInt
    datums: list[DatumCitation]
    nodes: list[NodeCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def totals(self) -> Self:
        paths = [datum.datum_path for datum in self.datums]
        if len(paths) != len(set(paths)):
            raise ValueError("Each datum must have one citation entry")
        if self.reference_count != sum(d.supporting_study_count for d in self.datums):
            raise ValueError("Citation reference count is inconsistent")
        return self
