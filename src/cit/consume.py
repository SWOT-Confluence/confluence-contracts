"""
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cit.contract import Consumes

CrossCheck = Literal["producer", "ignore"]
_CONSUMES_CONFIG = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ExternalSource(BaseModel):
    """One pipeline root no module produces."""

    model_config = _CONSUMES_CONFIG

    filepath: str
    cross_check: CrossCheck
    consumed_by: list[str] = Field(default_factory=list)
    why: str = ""
    sample: str | None = None
    variables: list[str] = Field(default_factory=list)


class ModuleDependencies(BaseModel):
    """One module's place in the Step Function, and the files it reads and writes."""

    model_config = _CONSUMES_CONFIG

    stage: int
    sfn_state: str
    produces_filepaths: list[str] = Field(default_factory=list)
    consumes: list[Consumes] = Field(default_factory=list)
    consumes_sample: str | None = None
    notes: str | None = None


class ConsumesRegistry(BaseModel):
    """Every module's interdependencies, as generated from a run mount."""

    model_config = _CONSUMES_CONFIG

    generated: str
    external_sources: list[ExternalSource] = Field(default_factory=list)
    modules: dict[str, ModuleDependencies] = Field(default_factory=dict)
