"""SoS metadata-rules model (the RULES side).

Models the committed rules artifact (``cit/resources/rules/sos_results_rules.yml``, generated from
``docs/sos-dataset/sos metadata.xlsx`` by ``tools/rules_convert.py``). This module is data only:
the checks that consume these models live in :class:`cit.validation.RulesValidator`, which keeps
the dependency one-directional and lets the validator be discovered with the rest.

Every field on :class:`MetadataRule` is optional because the artifact is sparse and partly
irregular -- 69 of its 147 variables carry no ``units``, a handful of bounds are strings such as
``'inf'``, and two ``coverage_content_type`` values are unresolved review comments rather than
codelist members. Tightening the types would make ``model_validate`` reject the artifact that
ships in the package.
"""

# Third-party imports
from pydantic import BaseModel, ConfigDict, Field

_RULES_CONFIG = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MetadataRule(BaseModel):
    """One variable's expected SoS attributes, as the spreadsheet declares them."""

    model_config = _RULES_CONFIG

    long_name: str | None = None
    comment: str | None = None
    units: str | None = None
    valid_min: float | str | None = None
    valid_max: float | str | None = None
    coverage_content_type: str | None = None


class MetadataRules(BaseModel):
    """One rules artifact: the produced file it governs, plus the metadata it expects.

    Attributes:
        module_name: The module whose produced file these rules govern.
        filepath: That file's path template, matched against a contract's ``produces``.
        global_attributes: The global attribute names the SoS spec requires.
        variable_attributes: Expected attributes, keyed by group then variable.
        fill_values: The canonical fill value for each type name (``Float``, ``Int``, ...).
    """

    model_config = _RULES_CONFIG

    module_name: str
    filepath: str
    global_attributes: list[str] = Field(default_factory=list)
    variable_attributes: dict[str, dict[str, MetadataRule]] = Field(default_factory=dict)
    fill_values: dict[str, float | int | str] = Field(default_factory=dict)
