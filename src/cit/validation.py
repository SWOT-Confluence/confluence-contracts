"""Structural validator plus the report-only health checks and registry cross-check.

Compares a contract (EXPECTED, :mod:`cit.contract`) against an actual file (ACTUAL,
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

from cit.contract import Produces, VariableContract
from cit.report import Finding, FindingStatus, FindingType
from cit.result import NetcdfResult, VarInfo
from cit.rules import MetadataRules


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
    rules: MetadataRules
    result: NetcdfResult
    strict: bool


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
                    validation="contract"
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
                    validation="contract"
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
                    validation="contract"
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
                    validation="contract"
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
                    validation="contract"
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
                    validation="contract",
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
                    validation="contract",
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
                    validation="contract"
                )
            )

        return findings


class RulesValidator(Validator):
    """"""

    FILL_ATTRS = ("_FillValue", "missing_value", "fill")
    TOKEN_TO_FILL_TYPES = {
        "f4": ("Float",),
        "f8": ("Float",),
        "i4": ("Int", "Int9"),
        "i8": ("Int", "Int9"),
        "S1": ("Char",),
        "str": ("Char",),
    }

    def validate(self, context: ValidatorContext):
        """"""
        if not context.rules:
            return []

        module_name = context.name
        filepath = context.rules.filepath
        rules = context.rules
        result = context.result
        strict = context.strict

        return [
            *self._check_global_attributes(rules.global_attributes, result.global_attributes.keys(), module_name, filepath, strict),
            *self._check_variables_attributes(rules.variable_attributes, result, rules.fill_values, module_name, filepath, strict)
        ]

    def _check_global_attributes(self, rule, result, module_name, filepath, strict):
        """"""
        missing, extra, common = _partition(rule, result)
        findings: list[Finding] = []

        for name in missing:
            findings.append(
                Finding(
                    type=FindingType.MISSING,
                    status=FindingStatus.FAIL if strict else FindingStatus.WARN,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                    validation="rule"
                )
            )

        for name in extra:
            findings.append(
                Finding(
                    type=FindingType.EXTRA,
                    status=FindingStatus.WARN,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                    validation="rule"
                )
            )

        for name in common:
            findings.append(
                Finding(
                    type=FindingType.PASSED,
                    status=FindingStatus.INFO,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                    validation="rule"
                )
            )

        return findings

    def _check_variables_attributes(self, rule, result, fill_values, module_name, filepath, strict):
        """"""

        rule_attributes = {
            f"{group}/{variable}": metadata_rule.model_fields_set
            for group, variables in rule.items()
            for variable, metadata_rule in variables.items()
        }

        result_attributes = {
            variable: set(attributes)
            for variable, attributes in result.variable_attributes.items()
        }

        missing, extra, common = _partition(rule_attributes.keys(), result_attributes.keys())
        findings: list[Finding] = []

        for name in missing:
            findings.append(
                Finding(
                    type=FindingType.MISSING,
                    status=FindingStatus.FAIL if strict else FindingStatus.WARN,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                    validation="rule"
                )
            )

        for name in extra:
            findings.append(
                Finding(
                    type=FindingType.EXTRA,
                    status=FindingStatus.WARN,
                    module_name=module_name,
                    component=name,
                    filepath=filepath,
                    validation="rule"
                )
            )

        for name in common:
            attributes = result.variable_attributes[name]

            variable_findings = self._check_variable_attributes(rule_attributes[name], result_attributes[name], name, module_name, filepath, strict)
            findings.extend(variable_findings)

            finding = self._check_valid_min_max(attributes, name, module_name, filepath, strict)
            if finding is not None:
                findings.append(finding)

            finding = self._check_fill_value(attributes, result.variables[name].dtype, fill_values, name, module_name, filepath, strict)
            if finding is not None:
                findings.append(finding)

        return findings

    def _check_variable_attributes(self, rule, result, var_name, module_name, filepath, strict):
        """"""
        missing, extra, common = _partition(rule, result)
        findings: list[Finding] = []

        for name in missing:
            findings.append(
                Finding(
                    type=FindingType.MISSING,
                    status=FindingStatus.FAIL if strict else FindingStatus.WARN,
                    module_name=module_name,
                    component=f"{var_name}.{name}",
                    filepath=filepath,
                    validation="rule"
                )
            )

        for name in extra:
            findings.append(
                Finding(
                    type=FindingType.EXTRA,
                    status=FindingStatus.WARN,
                    module_name=module_name,
                    component=f"{var_name}.{name}",
                    filepath=filepath,
                    validation="rule"
                )
            )

        for name in common:
            findings.append(
                Finding(
                    type=FindingType.PASSED,
                    status=FindingStatus.INFO,
                    module_name=module_name,
                    component=f"{var_name}.{name}",
                    filepath=filepath,
                    validation="rule"
                )
            )

        return findings

    def _check_valid_min_max(
        self, variable, var_name, module_name, filepath, strict
    ) -> Finding | None:
        """Check that a variable's valid_min does not exceed its valid_max.

        Both bounds are coerced with :func:`_numeric` before comparison, because netCDF reports
        them as numpy scalars and because the SoS spreadsheet carries a handful of non-numeric
        bounds (``'inf'``, ``'inf; 9.99999999998E11'``). A bound that will not coerce is skipped
        rather than guessed at.

        Args:
            variable: One variable's attributes, as read from the file.
            var_name: The group-qualified variable name, carried onto the finding.
            module_name: The module being validated.
            filepath: The produced file's contract path template.
            strict: When True, an inverted range fails the run rather than warning.

        Returns:
            A PASSED finding when the range is ordered, a DIFFERENT finding when it is inverted, or
            ``None`` when either bound is absent or non-numeric.
        """
        minimum = _numeric(variable.get("valid_min"))
        maximum = _numeric(variable.get("valid_max"))

        if minimum is None or maximum is None:
            return None

        if minimum <= maximum:
            return Finding(
                type=FindingType.PASSED,
                status=FindingStatus.INFO,
                module_name=module_name,
                component=var_name,
                filepath=filepath,
                validation="rule",
            )

        return Finding(
            type=FindingType.DIFFERENT,
            status=FindingStatus.FAIL if strict else FindingStatus.WARN,
            module_name=module_name,
            component=var_name,
            filepath=filepath,
            validation="rule",
            message=f"(valid_min: {minimum}) exceeds (valid_max: {maximum})",
        )


    def _check_fill_value(
        self, variable, dtype, fill_values, var_name, module_name, filepath, strict
    ) -> Finding | None:
        """Check a variable's declared fill value against the canonical value for its type.

        netCDF forbids ``_FillValue`` on VLEN types, so some variables carry ``missing_value`` or
        the non-standard ``fill`` instead, and some declare none at all. The first of
        :attr:`FILL_ATTRS` present wins; an absent declaration is not a violation, because the
        format itself prevents it.

        Args:
            variable: One variable's attributes, as read from the file.
            dtype: The variable's contract dtype token (``f8``, ``i4``, ``S1``, ``str``, ...).
            fill_values: The canonical fill values from the rules artifact, keyed by type name.
            var_name: The group-qualified variable name, carried onto the finding.
            module_name: The module being validated.
            filepath: The produced file's contract path template.
            strict: When True, a mismatch fails the run rather than warning.

        Returns:
            A PASSED finding when the declared fill value is canonical, a DIFFERENT finding when it
            is not or when the dtype has no canonical fill value, or ``None`` when the variable
            declares no fill value at all.
        """
        declared = next(
            ((name, variable[name]) for name in self.FILL_ATTRS if name in variable), None
        )
        if declared is None:
            return None

        attr_name, value = declared

        # An unmapped dtype means CIT cannot check this variable
        fill_types = self.TOKEN_TO_FILL_TYPES.get(dtype)
        if fill_types is None:
            return Finding(
                type=FindingType.DIFFERENT,
                status=FindingStatus.WARN,  # never escalated: a gap in CIT, not in the data
                module_name=module_name,
                component=f"{var_name}.{attr_name}",
                filepath=filepath,
                message=f"dtype {dtype!r} has no canonical fill value; fill value not checked",
                validation="rule",
            )

        # Indexed, not filtered: a missing key means a malformed rules artifact
        expected = [fill_values[name] for name in fill_types]
        if any(_same_fill(value, candidate) for candidate in expected):
            return Finding(
                type=FindingType.PASSED,
                status=FindingStatus.INFO,
                module_name=module_name,
                component=var_name,
                filepath=filepath,
                message=f"{value!r} is canonical for dtype {dtype!r}",
                validation="rule",
            )

        return Finding(
            type=FindingType.DIFFERENT,
            status=FindingStatus.FAIL if strict else FindingStatus.WARN,
            module_name=module_name,
            component=f"{var_name}.{attr_name}",
            filepath=filepath,
            message=f"(rule fill: {expected}) and (result fill: {value!r}) for dtype {dtype!r}",
            validation="rule",
        )


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


def _numeric(value: object) -> float | None:
    """Return value as a float, or None when it is not numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_fill(actual: object, expected: object) -> bool:
    """Compare a declared fill value against a canonical one, across bytes, str and numeric forms.

    An ``S1`` variable's ``_FillValue`` reads back from netCDF as ``bytes`` (``b"x"``) while the
    rules artifact holds the string ``"x"``, so bytes are decoded first. The string comparison must
    precede the numeric one: routing ``"x"`` through :func:`_numeric` yields ``None`` and would
    report every char variable as mismatched.

    Args:
        actual: The fill value declared on the variable.
        expected: A canonical fill value from the rules artifact.

    Returns:
        True when the two denote the same fill value. ``nan`` never matches, which is intended --
        it is not the SoS fill value for any type.
    """
    if isinstance(actual, bytes):  # np.bytes_ subclasses bytes, so this covers both
        actual = actual.decode()

    if isinstance(actual, str) or isinstance(expected, str):
        return str(actual) == str(expected)

    actual_number, expected_number = _numeric(actual), _numeric(expected)

    return (
        actual_number is not None
        and expected_number is not None
        and actual_number == expected_number
    )
