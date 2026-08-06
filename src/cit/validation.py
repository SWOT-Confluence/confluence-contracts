"""Structural validator plus the report-only health checks and registry cross-check.

Compares a contract (EXPECTED, :mod:`cit.models`) against an actual file (ACTUAL,
:mod:`cit.result`) and emits ``Finding``s. This is the core interop guarantee: a changed
module still produces the variables/dtypes/shapes downstream consumers expect.

Implements (P1-6):

- :class:`Validator` -- the abstract base every check implements, with a self-populating
  registry so :meth:`Validator.discover` can instantiate each concrete validator.
- :class:`ValidatorContext` -- the EXPECTED/ACTUAL pair one validator run works over.
- :class:`ContractValidator` -- the structural check. Per declared dimension and variable:
  existence (missing+required -> FAIL, missing+optional -> WARN), dtype match, and dimension
  match by name *and order*. An undeclared component in the file -> WARN (drift), asymmetric
  by design. Dimension *sizes* are deliberately not compared: they vary per run, and netCDF
  derives a variable's shape from its dimensions, so matching names in order is sufficient.

Planned:

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
from cit.report import Finding, FindingStatus, FindingType
from cit.result import NetcdfResult, VarInfo
from cit.rules import Rule


@dataclass
class ValidatorContext:
    """Everything one validator run needs: the EXPECTED contract and the ACTUAL file.

    Attributes:
        name: The module being validated (e.g. ``momma``), carried onto every finding.
        contract: The declared interface for one produced file (the EXPECTED side).
        rules: The SoS metadata rules, for validators that lint attributes (P1-15).
        result: The read model for the produced file being checked (the ACTUAL side).
    """

    name: str
    contract: Produces
    rules: list[Rule]
    result: NetcdfResult


class Validator(ABC):
    """Abstract base for one family of checks over a :class:`ValidatorContext`.

    Subclasses are registered automatically as they are defined, so a new validator becomes
    part of a run by existing -- no registration call and no wiring in the orchestrator.
    """

    _registry: list[type["Validator"]] = []

    def __init_subclass__(cls, **kwargs) -> None:
        """Register every subclass as it is defined."""
        super().__init_subclass__(**kwargs)
        Validator._registry.append(cls)

    @classmethod
    def discover(cls) -> list["Validator"]:
        """Instantiate one of every concrete Validator subclass.

        Importing a validator module is what registers it, so any module defining validators
        must be imported here.

        Returns:
            One instance of each registered non-abstract subclass.
        """
        import cit.validation  # noqa: F401 registers ContractValidator

        # add future validator modules here as they land if not in this file
        return [sub() for sub in cls._registry if not inspect.isabstract(sub)]

    @abstractmethod
    def validate(self, context: ValidatorContext) -> list[Finding]:
        """Run this validator's checks over one contract/result pair.

        Args:
            context: The EXPECTED contract and ACTUAL file to compare.

        Returns:
            Every finding this validator produced; empty when it has nothing to report.
        """
        ...


class ContractValidator(Validator):
    """Compare a contract's declared structure against what a produced file actually holds.

    Checks dimensions and variables for existence in both directions, then dtype and dimension
    ordering for the variables present on both sides.
    """

    def validate(self, context: ValidatorContext) -> list[Finding]:
        """Check every declared dimension and variable against the produced file.

        Args:
            context: The EXPECTED contract and ACTUAL file to compare.

        Returns:
            The dimension findings followed by the variable findings.
        """
        module_name = context.name
        contract = context.contract
        result = context.result

        return [
            *self._check_dimensions(module_name, contract, result),
            *self._check_variables(module_name, contract, result),
        ]

    def _check_dimensions(
        self, module_name: str, contract: Produces, result: NetcdfResult
    ) -> list[Finding]:
        """Check that the file's dimensions match the ones the contract declares.

        Only presence is compared. A dimension's size varies per run (``nt`` is the reach's
        timestep count), so a contract cannot declare it.

        Args:
            module_name: The module being validated, carried onto every finding.
            contract: The declared interface for this produced file.
            result: The read model for the produced file.

        Returns:
            One finding per dimension: FAIL if declared but absent, WARN if present but
            undeclared, INFO if present on both sides.
        """
        missing, extra, common = _partition(contract.dimensions, result.dimensions)
        findings: list[Finding] = []

        for name in missing:
            findings.append(
                Finding(
                    type=FindingType.MISSING,
                    status=FindingStatus.FAIL,
                    module_name=module_name,
                    component=name,
                    filepath=contract.filepath,
                )
            )

        for name in extra:
            findings.append(
                Finding(
                    type=FindingType.EXTRA,
                    status=FindingStatus.WARN,
                    module_name=module_name,
                    component=name,
                    filepath=contract.filepath,
                )
            )

        for name in common:
            findings.append(
                Finding(
                    type=FindingType.PASSED,
                    status=FindingStatus.INFO,
                    module_name=module_name,
                    component=name,
                    filepath=contract.filepath,
                )
            )

        return findings

    def _check_variables(
        self, module_name: str, contract: Produces, result: NetcdfResult
    ) -> list[Finding]:
        """Check the file's variables against the ones the contract declares.

        Existence is checked in both directions; the variables present on both sides are then
        handed to :meth:`_check_variable` for their structure.

        Args:
            module_name: The module being validated, carried onto every finding.
            contract: The declared interface for this produced file.
            result: The read model for the produced file.

        Returns:
            A finding for every declared-but-absent variable (FAIL when required, WARN when
            optional) and every undeclared file variable (WARN), plus the structural findings
            for the variables present on both sides.
        """
        missing, extra, common = _partition(contract.variables, result.variables)
        findings: list[Finding] = []

        for name in missing:
            status = FindingStatus.FAIL if contract.variables[name].required else FindingStatus.WARN
            findings.append(
                Finding(
                    type=FindingType.MISSING,
                    status=status,
                    module_name=module_name,
                    component=name,
                    filepath=contract.filepath,
                )
            )

        for name in extra:
            findings.append(
                Finding(
                    type=FindingType.EXTRA,
                    status=FindingStatus.WARN,
                    module_name=module_name,
                    component=name,
                    filepath=contract.filepath,
                )
            )

        for name in common:
            variable_findings = self._check_variable(
                module_name,
                contract.filepath,
                name,
                contract.variables[name],
                result.variables[name],
            )
            findings.extend(variable_findings)

        return findings

    def _check_variable(
        self,
        module_name: str,
        filepath: str,
        name: str,
        contract: VariableContract,
        result: VarInfo,
    ) -> list[Finding]:
        """Compare one variable's dtype and dimensions against its contract.

        The two checks are independent, so a variable with both a wrong dtype and wrong
        dimensions reports both. Dimensions are compared as ordered tuples: ``[nx, nt]`` and
        ``(nt, nx)`` index differently for a downstream consumer and so must not match.

        Args:
            module_name: The module being validated, carried onto every finding.
            filepath: The produced file this variable came from.
            name: The variable's name.
            contract: The variable's declared structure (the EXPECTED side).
            result: The variable's structure as read from the file (the ACTUAL side).

        Returns:
            One DIFFERENT/FAIL finding per disagreeing attribute, or a single PASSED/INFO
            finding when both checks agree.
        """
        findings: list[Finding] = []
        matched = True

        if contract.dtype != result.dtype:
            message = f"(contract dtype: {contract.dtype}) and (result dtype: {result.dtype})"
            findings.append(
                Finding(
                    type=FindingType.DIFFERENT,
                    status=FindingStatus.FAIL,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                    message=message,
                )
            )
            matched = False

        if tuple(contract.dimensions) != result.dims:
            message = f"(contract shape: {contract.dimensions}) and (result shape: {result.shape})"
            findings.append(
                Finding(
                    type=FindingType.DIFFERENT,
                    status=FindingStatus.FAIL,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                    message=message,
                )
            )
            matched = False

        if matched:
            findings.append(
                Finding(
                    type=FindingType.PASSED,
                    status=FindingStatus.INFO,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                )
            )

        return findings


def _partition(
    contract: Iterable[str], result: Iterable[str]
) -> tuple[list[str], list[str], list[str]]:
    """Split two sets of component names into what is missing, extra, and common to both.

    Both directions come from the same pair of set differences, so the contract-against-result
    and result-against-contract comparisons are one operation rather than two traversals. A
    mapping may be passed for either side; iterating one yields its keys.

    Args:
        contract: The names the contract declares.
        result: The names the produced file actually holds.

    Returns:
        A ``(missing, extra, common)`` triple -- declared but absent, present but undeclared,
        and present on both sides -- each sorted so a report's output is deterministic.
    """
    contract_names = set(contract)
    result_names = set(result)

    return (
        sorted(contract_names - result_names),  # missing
        sorted(result_names - contract_names),  # extra
        sorted(contract_names & result_names),  # matched
    )
