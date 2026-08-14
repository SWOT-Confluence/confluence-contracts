"""SoS metadata-rules model and validator (the RULES side).

Loads the committed rules artifact (``cit/resources/rules/sos_results_rules.yml``, generated from
``docs/sos-dataset/sos metadata.xlsx`` by a dev-time converter) and checks metadata
attributes against the SoS specification.

Planned ``RulesValidation.validate_rules(target, module, strict=False)`` runs in two modes:

- lint a ``Contract`` -- every declared variable must carry SoS-compliant ``attrs``;
- check an aggregated SoS/Output result file's variable and global attributes.

Checks include: required global attributes present; per-variable required attributes
present; non-empty ``units``; ``coverage_content_type`` in the ISO codelist;
``valid_min <= valid_max``; ``_FillValue`` matching the fill value for its type. With
``strict=True`` violations FAIL; otherwise they WARN.
"""

# Third-party imports
from pydantic import BaseModel, ConfigDict, Field


_RULES_CONFIG = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MetadataRules(BaseModel):
    """"""

    model_config = _RULES_CONFIG
    module_name: str
    filepath: str
    global_attributes: list[str] = Field(default_factory=list)
    variable_attributes: dict[str, dict[str, MetadataRule]] = Field(default_factory=dict)
    fill_values: dict[str, float | int | str] = Field(default_factory=dict)


class MetadataRule(BaseModel):
    """"""

    model_config = _RULES_CONFIG
    long_name: str | None = None
    comment: str | None = None
    units: str | None = None
    valid_min: float | str | None = None
    valid_max: float | str | None = None
    coverage_content_type: str | None = None
