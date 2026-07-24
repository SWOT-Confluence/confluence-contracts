"""Pydantic v2 models describing a module contract (the EXPECTED side).

These models are the in-memory representation of a ``contracts/<module>.yml`` file:
the declared interface a Confluence module promises to produce. They are the source
of truth from which the committed JSON Schema is derived (see :mod:`cit.schema`).

Planned classes (P1-2), outer to inner:

- ``Contract`` -- top-level document: module, confluence version, and source provenance.
- ``ModuleContract`` -- the module's name plus what it ``produces`` and ``consumes``.
- ``Produces`` -- one output file: path template, dimensions, and variables.
- ``Consumes`` -- inputs a module reads (feeds the consumes/produces cross-check).
- ``VariableContract`` -- one variable's structure: dtype, shape, required.
- ``VariableAttrs`` -- SoS metadata attributes linted by :mod:`cit.rules`.
- ``Source`` -- provenance: repo, github_username, branch, commit, image_tag.

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

DataType = Literal[
    "f4",
    "f8",
    "i4",
    "i8",
    "S1"
]


class _Base(BaseModel):
    """Shared configuration data."""

    model_config = ConfigDict(
        extra="forbid",             # no extra data may be passed in to the classes
        frozen=True,                # make instances immutable
        str_strip_whitespace=True,  # strip whitespace on string fields
        validate_default=True,      # validate default values
        use_enum_values=True        # store enum's value rather than member
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
        """Reject a variable whose valid_min exceeds valid_max."""
        if (
            self.valid_min is not None
            and self.valid_max is not None
            and self.valid_min > self.valid_max
        ):
            raise ValueError(
                f"valid_min ({self.valid_min}) > valid_max ({self.valid_max})"
            )
        return self


class VariableContract(_Base):
    """One variable's declarative structure: dtype, shape, requiredness, metadata."""

    dtype: DataType
    shape: list[str] = Field(default_factory=list)  # dimensions list
    required: bool = True
    attrs: VariableAttrs | None = None


class Produces(_Base):
    """One output file: path template, its dimensions, and its variables."""

    filepath: str
    dimensions: list[str] = Field(default_factory=list)
    variables: dict[str, VariableContract]


class Consumes(_Base):
    """One input a module reads (feeds the consumers/produces cross-check)."""

    filepath: str
    variables: list[str] = Field(default_factory=list)


class ModuleContract(_Base):
    """The modules contract that guides what it produces/consumes."""

    name: str
    produces: list[Produces] = Field(default_factory=list)
    consumes: list[Consumes] = Field(default_factory=list)


class Contract(_Base):
    """Top-level document: module, version, provenance."""

    version: str  # confluence version contract targets
    source: Source
    module: ModuleContract



