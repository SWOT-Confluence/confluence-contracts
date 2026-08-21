"""Pydantic v2 models describing a module contract (the EXPECTED side).

These models are the in-memory representation of a ``contracts/<module>.yml`` file: the
declared interface a Confluence module promises to produce, and the source from which the
committed JSON Schema is derived (see :mod:`cit.schema`).

This module holds the **contract** models only; the generated SoS metadata-rules artifact's
models live in :mod:`cit.rules`. They are kept apart because a contract is hand-reviewed and
strictly typed, while a rules artifact is machine-generated from a spreadsheet and must
tolerate sparse and irregular values.

All models use ``extra="forbid"`` so an unexpected key is an error, not a silent typo.
"""

# Standard imports
from __future__ import annotations

from typing import Literal

# Third-party imports
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ISO 19115-1 / ACDD codelist for coverage_content_type (SoS metadata convention).
CoverageContentType = Literal[
    "image",
    "thematicClassification",
    "physicalMeasurement",
    "auxiliaryInformation",
    "qualityInformation",
    "referenceInformation",
    "modelResult",
    "coordinate",
]

DataType = Literal["f4", "f8", "i4", "i8", "S1", "str"]


class _Base(BaseModel):
    """Shared configuration data."""

    model_config = ConfigDict(
        extra="forbid",  # no extra data may be passed in to the classes
        frozen=True,  # make instances immutable
        str_strip_whitespace=True,  # strip whitespace on string fields
        validate_default=True,  # validate default values
        use_enum_values=True,  # store enum's value rather than member
        coerce_numbers_to_str=True,  # coerce version floats to string
    )


class Source(_Base):
    """Provenance: Which functional version of the module this contract targets."""

    repo: str
    github_username: str
    branch: str
    commit: str
    image_tag: str


class VariableAttrs(_Base):
    """SoS metadata attributes for one variable (linted by cit.rules)."""

    long_name: str
    comment: str | None = None
    units: str
    valid_min: float | None = None
    valid_max: float | None = None
    coverage_content_type: CoverageContentType

    @model_validator(mode="after")
    def _check_bounds(self) -> VariableAttrs:
        """Reject a variable whose valid_min exceeds valid_max.

        Returns:
            This instance, unchanged, once the bounds check passes.
        """
        if (
            self.valid_min is not None
            and self.valid_max is not None
            and self.valid_min > self.valid_max
        ):
            raise ValueError(f"valid_min ({self.valid_min}) > valid_max ({self.valid_max})")
        return self


class VariableContract(_Base):
    """One variable's declarative structure: dtype, dimensions, requiredness, metadata."""

    dtype: DataType
    dimensions: list[str] = Field(default_factory=list)  # dimension names, in order
    required: bool = True
    attrs: VariableAttrs | None = None


class Produces(_Base):
    """One output file: path template, its dimensions, and its variables."""

    filepath: str
    dimensions: list[str] = Field(default_factory=list)
    variables: dict[str, VariableContract]


class Consumes(_Base):
    """One input a module reads (feeds the consumes/produces cross-check)."""

    filepath: str
    variables: list[str] = Field(default_factory=list)


class ModuleContract(_Base):
    """A module's contract that guides what it produces/consumes."""

    name: str
    produces: list[Produces] = Field(default_factory=list)
    consumes: list[Consumes] = Field(default_factory=list)


class Contract(_Base):
    """Top-level document: module, version, provenance."""

    version: str  # confluence version contract targets
    source: Source
    module: ModuleContract
