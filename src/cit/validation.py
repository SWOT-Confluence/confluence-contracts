"""Structural validator plus the report-only health checks and registry cross-check.

Compares a contract (EXPECTED, :mod:`cit.models`) against an actual file (ACTUAL,
:mod:`cit.result`) and emits ``Finding``s. This is the core interop guarantee: a changed
module still produces the variables/dtypes/shapes downstream consumers expect.

Planned:

- ``ResultsValidation.compare_contract_results(contract, result) -> list[Finding]`` (P1-6):
  per variable, existence (missing+required -> FAIL, missing+optional -> INFO), dtype match,
  shape/dims match; declared dimensions present; an undeclared file variable -> WARN (drift),
  asymmetric by design.
- ``check_completeness`` / ``check_non_fill`` (P1-16): report-only guards against silent
  partial or all-fill output; they never fail the run, even under ``--strict``.
- ``cross_check(registry) -> list[Finding]`` (P1-17): a files-closed lint -- every declared
  ``consumes`` variable must be produced by some contract, or it FAILs naming the orphan.
"""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass

from cit.models import Produces, VariableContract
from cit. report import Finding, FindingStatus, FindingType
from cit.result import Result, VarInfo
from cit.rules import Rule


@dataclass
class ValidatorContext():
    name: str
    contract: Produces
    rules: list[Rule]
    result: Result


class Validator(ABC):

    _registry: list[type["Validator"]] = []

    def __init_subclass__(cls, **kwargs) -> None:
        """Register every subclass as it is defined."""
        super().__init_subclass__(**kwargs)
        Validator._registry.append(cls)

    @classmethod
    def discover(cls) -> list["Validator"]:
        """"""
        """Instantiate one of every concrete Validator subclass."""
        import cit.validation  # noqa: F401 registers ContractValidator
        # add future validator modules here as they land if not in this file
        return [sub() for sub in cls._registry if not inspect.isabstract(sub)]

    @abstractmethod
    def validate(self, context: ValidatorContext) -> list[Finding]:
        """"""
        ...


class ContractValidator(Validator):
    """"""

    def validate(self, context: ValidatorContext) -> list[Finding]:
        """"""
        module_name = context.name
        contract = context.contract
        result = context.result

        return [
            *self._check_dimensions(module_name, contract, result),
            *self._check_variables(module_name, contract, result),
        ]

    def _check_dimensions(self, module_name: str, contract: Produces, result: Result) -> list[Finding]:
        """"""
        missing, extra, common = _partition(contract.dimensions, result.dimensions)
        findings = []

        for name in missing:
            findings.append(Finding(
                type=FindingType.MISSING,
                status=FindingStatus.FAIL,
                module_name=module_name,
                component=name,
                filepath=contract.filepath
            ))

        for name in extra:
            findings.append(Finding(
                type=FindingType.EXTRA,
                status=FindingStatus.WARN,
                module_name=module_name,
                component=name,
                filepath=contract.filepath
            ))

        for name in common:
            findings.append(Finding(
                type=FindingType.PASSED,
                status=FindingStatus.INFO,
                module_name=module_name,
                component=name,
                filepath=contract.filepath
            ))

        return findings

    def _check_variables(self, module_name: str, contract: Produces, result: Result) -> list[Finding]:
        """"""
        missing, extra, common = _partition(contract.variables, result.variables)
        findings = []

        for name in missing:
            status = FindingStatus.FAIL if contract.variables[name].required else FindingStatus.WARN
            findings.append(Finding(
                type=FindingType.MISSING,
                status=status,
                module_name=module_name,
                component=name,
                filepath=contract.filepath
            ))

        for name in extra:
            findings.append(Finding(
                type=FindingType.EXTRA,
                status=FindingStatus.WARN,
                module_name=module_name,
                component=name,
                filepath=contract.filepath
            ))

        for name in common:
            variable_findings = self._check_variable(
                module_name,
                contract.filepath, name,
                contract.variables[name],
                result.variables[name])
            findings.extend(variable_findings)

        return findings

    def _check_variable(self, module_name:str, filepath: str, name: str, contract: VariableContract, result: VarInfo) -> list[Finding]:
        """"""
        findings = []
        matched = True

        if contract.dtype != result.dtype:
            message = f"(contract dtype: {contract.dtype}) and (result dtype: {result.dtype})"
            findings.append(Finding(
                type=FindingType.DIFFERENT,
                status=FindingStatus.FAIL,
                module_name=module_name,
                component=name,
                filepath=filepath,
                message=message
            ))
            matched = False

        if tuple(contract.dimensions) != result.dims:
            message = f"(contract shape: {contract.dimensions}) and (result shape: {result.shape})"
            findings.append(Finding(
                type=FindingType.DIFFERENT,
                status=FindingStatus.FAIL,
                module_name=module_name,
                component=name,
                filepath=filepath,
                message=message
            ))
            matched = False

        if matched:
            findings.append(Finding(
                type=FindingType.PASSED,
                status=FindingStatus.INFO,
                module_name=module_name,
                component=name,
                filepath=filepath
            ))

        return findings


def _partition(contract: Iterable[str], result: Iterable[str]) -> tuple[list[str], list[str], list[str]]:
        """"""
        contract_names = set(contract)
        result_names = set(result)

        return (
            sorted(contract_names - result_names),  # missing
            sorted(result_names - contract_names),  # extra
            sorted(contract_names & result_names),  # matched
        )
