"""Validated value objects stored inside canonical entities (JSON columns).

These are domain rules, not presentation: they define what an Evidence Profile status
*means* and refuse data that would contradict that meaning.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import AlternativeExplanationStatus, AttributeBasis, ProfileStatus

PROFILE_SECTIONS: Final[tuple[str, ...]] = (
    "identity",
    "integrity",
    "provenance",
    "quality",
    "acquisition_context",
    "classification",
)

PROFILE_STATUS_DEFINITIONS: Final[dict[ProfileStatus, str]] = {
    ProfileStatus.VERIFIED: (
        "Every attribute in this section was computed by a recorded, reproducible "
        "VERITAS procedure."
    ),
    ProfileStatus.PARTIAL: (
        "Some attributes are recorded, but at least one is declared (unchecked) or missing."
    ),
    ProfileStatus.UNKNOWN: (
        "The information was sought but could not be established from what is available."
    ),
    ProfileStatus.NOT_AVAILABLE: (
        "No procedure that could establish this section exists in the current version, "
        "or the section does not apply to this evidence."
    ),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProfileAttribute(_Frozen):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, max_length=500)
    basis: AttributeBasis


class ProfileSection(_Frozen):
    status: ProfileStatus
    attributes: tuple[ProfileAttribute, ...] = ()
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _status_matches_basis(self) -> ProfileSection:
        valued = [a for a in self.attributes if a.value is not None]
        all_computed = bool(self.attributes) and all(
            a.value is not None and a.basis is AttributeBasis.COMPUTED for a in self.attributes
        )
        if self.status is ProfileStatus.VERIFIED and not all_computed:
            raise ValueError("status 'verified' requires every attribute to have a computed value")
        if self.status is ProfileStatus.PARTIAL and not valued:
            raise ValueError("status 'partial' requires at least one recorded attribute value")
        if self.status in (ProfileStatus.UNKNOWN, ProfileStatus.NOT_AVAILABLE) and valued:
            raise ValueError(f"status '{self.status}' cannot carry attribute values")
        if self.status in (ProfileStatus.UNKNOWN, ProfileStatus.NOT_AVAILABLE) and not self.note:
            raise ValueError(f"status '{self.status}' requires an explanatory note")
        return self


class AlternativeExplanation(_Frozen):
    explanation: str = Field(min_length=1, max_length=1000)
    status: AlternativeExplanationStatus = AlternativeExplanationStatus.OPEN
    basis: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _exclusion_requires_basis(self) -> AlternativeExplanation:
        if self.status is AlternativeExplanationStatus.EXCLUDED and not self.basis:
            raise ValueError("an excluded alternative explanation requires a recorded basis")
        return self
