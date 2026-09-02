"""Structural validator plus the report-only health checks and registry cross-check.

Compares a contract (EXPECTED, :mod:`cit.contract`) against an actual file (ACTUAL,
:mod:`cit.result`) and emits ``Finding``s -- the core interop guarantee that a changed module
still produces the variables/dtypes/shapes downstream consumers expect.

:class:`Validator` is the abstract base every check implements, with a self-populating registry
so :meth:`Validator.discover` can instantiate each one with no wiring in the orchestrator.
:class:`ContractValidator` is the structural check: existence (missing+required FAILs,
missing+optional WARNs), dtype, and dimension match by name *and order*. An undeclared
component in the file WARNs rather than FAILs -- drift, not a violation, asymmetric by design.
Dimension *sizes* are deliberately not compared: they vary per run, and netCDF derives a
variable's shape from its dimensions, so matching names in order is sufficient.
"""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from cit.contract import Produces, VariableContract
from cit.report import Check, Finding, FindingStatus, FindingType, ValidationSource
from cit.result import NetcdfResult, VarInfo
from cit.rules import MetadataRules


@dataclass
class ValidatorContext:
    """Everything one validator run needs: the EXPECTED contract and the ACTUAL file.

    Attributes:
        name: The module being validated (e.g. ``momma``), carried onto every finding.
        contract: The declared interface for one produced file (the EXPECTED side).
        rules: The SoS metadata rules, for validators that lint attributes.
        result: The read model for the produced file being checked (the ACTUAL side).
    """

    name: str
    contract: Produces
    rules: MetadataRules
    result: NetcdfResult
    strict: bool = False


def _status(escalate: bool) -> FindingStatus:
    """Return FAIL when a finding should fail the run, WARN otherwise.

    Escalated by ``--strict`` for rule violations, and by ``required`` for contract variables.

    Args:
        escalate: Whether this finding should fail the run rather than just warn.

    Returns:
        FAIL when ``escalate``, else WARN.
    """
    return FindingStatus.FAIL if escalate else FindingStatus.WARN


@dataclass(frozen=True)
class _Reporter:
    """Binds the ``Finding`` fields that are invariant across one validator run.

    Attributes:
        module_name: The module being validated (e.g. ``momma``).
        filepath: The produced file's contract path template.
        validation: Which validation produced the finding (see :class:`ValidationSource`).
        results_file: The resolved produced file this run is checking, set once per file from
            ``result.filepath``. Defaults to ``""`` for callers (e.g. tests) built before a
            result file is known.
    """

    module_name: str
    filepath: str
    validation: ValidationSource
    results_file: str = ""

    def finding(
        self,
        finding_type: FindingType,
        status: FindingStatus,
        component: str,
        message: str = "",
        *,
        scope: str,
        check: Check,
        parent: str = "",
    ) -> Finding:
        """Build one finding, filling in the run-invariant fields.

        Args:
            finding_type: What the check found.
            status: How the finding bears on the exit policy.
            component: The dimension, variable or attribute this finding is about.
            message: Optional detail, e.g. the disagreeing expected and actual values.
            scope: What kind of thing was examined (``dimension``, ``variable``, ``attribute``,
                or ``global_attribute``).
            check: The specific question asked about it (see :class:`Check`).
            parent: The variable name this finding nests under in the report, for an
                attribute-scoped finding; ``""`` (the default) for every other scope.

        Returns:
            The constructed :class:`Finding`.
        """
        return Finding(
            type=finding_type,
            status=status,
            module_name=self.module_name,
            component=component,
            filepath=self.filepath,
            message=message,
            validation=self.validation,
            results_file=self.results_file,
            scope=scope,
            check=check,
            parent=parent,
        )

    def partition(
        self,
        expected: Iterable[str],
        actual: Iterable[str],
        *,
        scope: str,
        check: Check,
        component: Callable[[str], str] = str,
        missing_status: FindingStatus | Callable[[str], FindingStatus] = FindingStatus.FAIL,
        on_common: Callable[[str], list[Finding]] | None = None,
        parent: str = "",
    ) -> list[Finding]:
        """Emit findings for one expected-against-actual name comparison.

        Every check here shares this shape -- split two sets of names, report each bucket -- so
        the shape lives once and callers supply only what differs.

        Args:
            expected: The names the EXPECTED side declares.
            actual: The names the produced file actually holds.
            scope: What kind of thing was examined, carried onto every finding produced.
            check: The specific question asked (see :class:`Check`), also carried onto every
                finding produced.
            component: Maps a name to the finding's component, for qualifying a name under its
                parent (e.g. an attribute under its variable).
            missing_status: The status for a declared-but-absent name, or a callable returning
                one per name -- used where requiredness varies per variable.
            on_common: What to emit for a name present on both sides. Defaults to a single
                PASSED finding; a per-item check returns its own findings instead.
            parent: The variable name every finding this call produces nests under; ``""`` when
                the comparison is not attribute-scoped.

        Returns:
            The missing findings, then the extra ones, then whatever ``common`` produced.
        """
        missing, extra, common = partition(expected, actual)
        findings: list[Finding] = []

        for name in missing:
            status = missing_status(name) if callable(missing_status) else missing_status
            findings.append(
                self.finding(
                    FindingType.MISSING,
                    status,
                    component(name),
                    scope=scope,
                    check=check,
                    parent=parent,
                )
            )

        for name in extra:
            findings.append(
                self.finding(
                    FindingType.EXTRA,
                    FindingStatus.WARN,
                    component(name),
                    scope=scope,
                    check=check,
                    parent=parent,
                )
            )

        for name in common:
            findings.extend(
                on_common(name)
                if on_common is not None
                else [
                    self.finding(
                        FindingType.PASSED,
                        FindingStatus.INFO,
                        component(name),
                        scope=scope,
                        check=check,
                        parent=parent,
                    )
                ]
            )

        return findings


class Validator(ABC):
    """Abstract base for one family of checks over a :class:`ValidatorContext`.

    Subclasses are registered automatically as they are defined, so a new validator becomes
    part of a run by existing -- no registration call and no wiring in the orchestrator.
    """

    _registry: list[type["Validator"]] = []

    def __init_subclass__(cls, **kwargs) -> None:
        """Register every subclass as it is defined.

        Args:
            kwargs: Forwarded unchanged to ``super().__init_subclass__``.
        """
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
        contract = context.contract
        result = context.result
        report = _Reporter(
            context.name, contract.filepath, ValidationSource.STRUCTURE, result.filepath
        )

        return [
            *self._check_dimensions(report, contract, result),
            *self._check_variables(report, contract, result),
        ]

    def _check_dimensions(
        self, report: _Reporter, contract: Produces, result: NetcdfResult
    ) -> list[Finding]:
        """Check that the file's dimensions match the ones the contract declares.

        Only presence is compared. A dimension's size varies per run (``nt`` is the reach's
        timestep count), so a contract cannot declare it.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            contract: The declared interface for this produced file (the EXPECTED side).
            result: The read model for the produced file being checked (the ACTUAL side).

        Returns:
            One finding per dimension: FAIL if declared but absent, WARN if present but
            undeclared, INFO if present on both sides.
        """
        return report.partition(
            contract.dimensions, result.dimensions, scope="dimension", check=Check.EXISTS
        )

    def _check_variables(
        self, report: _Reporter, contract: Produces, result: NetcdfResult
    ) -> list[Finding]:
        """Check the file's variables against the ones the contract declares.

        Existence is checked in both directions; the variables present on both sides are then
        handed to :meth:`_check_variable` for their structure.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            contract: The declared interface for this produced file (the EXPECTED side).
            result: The read model for the produced file being checked (the ACTUAL side).

        Returns:
            A finding for every declared-but-absent variable (FAIL when required, WARN when
            optional) and every undeclared file variable (WARN), plus the structural findings
            for the variables present on both sides.
        """
        return report.partition(
            contract.variables,
            result.variables,
            scope="variable",
            check=Check.EXISTS,
            missing_status=lambda name: _status(contract.variables[name].required),
            on_common=lambda name: self._check_variable(
                report, name, contract.variables[name], result.variables[name]
            ),
        )

    def _check_variable(
        self,
        report: _Reporter,
        name: str,
        contract: VariableContract,
        result: VarInfo,
    ) -> list[Finding]:
        """Compare one variable's dtype and dimensions against its contract.

        The two checks are independent, so a variable with both a wrong dtype and wrong
        dimensions reports both. Dimensions are compared as ordered tuples: ``[nx, nt]`` and
        ``(nt, nx)`` index differently for a downstream consumer and so must not match.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            name: The variable's qualified name (``group/var`` when nested).
            contract: This variable's declared structure (the EXPECTED side).
            result: This variable's structure as the file actually holds it (the ACTUAL side).

        Returns:
            One DIFFERENT/FAIL finding per disagreeing attribute, or a single PASSED/INFO
            finding when both checks agree.
        """
        findings: list[Finding] = []

        if contract.dtype != result.dtype:
            findings.append(
                report.finding(
                    FindingType.DIFFERENT,
                    FindingStatus.FAIL,
                    name,
                    f"(contract dtype: {contract.dtype}) and (result dtype: {result.dtype})",
                    scope="variable",
                    check=Check.DTYPE,
                )
            )

        if tuple(contract.dimensions) != result.dims:
            findings.append(
                report.finding(
                    FindingType.DIFFERENT,
                    FindingStatus.FAIL,
                    name,
                    f"(contract dims: {contract.dimensions}) and (result dims: {result.dims})",
                    scope="variable",
                    check=Check.DIMS,
                )
            )

        return findings or [
            report.finding(
                FindingType.PASSED,
                FindingStatus.INFO,
                name,
                scope="variable",
                check=Check.DTYPE_DIMS,
            )
        ]


class RulesValidator(Validator):
    """Lint a produced file's metadata against the SoS specification.

    Where :class:`ContractValidator` checks that the data is shaped correctly, this checks that it
    is *documented* correctly: the SoS file is the product published to PO.DAAC, so its CF and ACDD
    attribute conventions are an interface guarantee like any other. Checks the required global
    attributes, then per variable its attribute set, its ``valid_min``/``valid_max`` ordering, and
    its fill value.

    Modules with no rules artifact are skipped, which is what scopes these checks to the SoS
    product rather than to every module's intermediate files.
    """

    # The three VariableAttrs fields with no default. Fixed rather than read from the artifact,
    # which carries no required flag and omits `units` on 69 of its 147 variables.
    REQUIRED_ATTRS = ("long_name", "units", "coverage_content_type")
    FILL_ATTRS = ("_FillValue", "missing_value", "fill")
    TOKEN_TO_FILL_TYPES = {
        "f4": ("Float",),
        "f8": ("Float",),
        "i4": ("Int", "Int9"),
        "i8": ("Int", "Int9"),
        "S1": ("Char",),
        "str": ("Char",),
    }

    def validate(self, context: ValidatorContext) -> list[Finding]:
        """Check one produced file's metadata against the SoS rules.

        Args:
            context: The EXPECTED contract and ACTUAL file to compare.

        Returns:
            The global-attribute findings followed by the per-variable ones; empty when this
            module has no rules artifact.
        """
        if not context.rules:
            return []

        rules = context.rules
        result = context.result
        strict = context.strict
        report = _Reporter(context.name, rules.filepath, ValidationSource.METADATA, result.filepath)

        return [
            *self._check_global_attributes(report, rules.global_attributes, result, strict),
            *self._check_variables_attributes(report, rules, result, strict),
        ]

    def _check_global_attributes(
        self, report: _Reporter, rule: Iterable[str], result: NetcdfResult, strict: bool
    ) -> list[Finding]:
        """Check the file's global attributes against the ones the SoS spec requires.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            rule: The global attribute names the SoS spec requires.
            result: The read model for the produced file being checked (the ACTUAL side).
            strict: When True, a missing attribute FAILs the run rather than WARN.

        Returns:
            One finding per global attribute.
        """
        return report.partition(
            rule,
            result.global_attributes,
            scope="global_attribute",
            check=Check.EXISTS,
            missing_status=_status(strict),
        )

    def _check_variables_attributes(
        self, report: _Reporter, rules: MetadataRules, result: NetcdfResult, strict: bool
    ) -> list[Finding]:
        """Check every variable's metadata against the SoS spec, in both directions.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            rules: This module's SoS metadata rules artifact.
            result: The read model for the produced file being checked (the ACTUAL side).
            strict: When True, a rule violation FAILs the run rather than WARN.

        Returns:
            The variable-level findings, with each common variable's own findings folded in.
        """
        # All rules against all results, compared directly on `group/variable` keys.
        rule_attributes = {
            f"{group}/{variable}": metadata_rule.model_fields_set
            for group, variables in rules.variable_attributes.items()
            for variable, metadata_rule in variables.items()
        }

        result_attributes = {
            variable: set(attributes) for variable, attributes in result.variable_attributes.items()
        }

        findings = report.partition(
            rule_attributes,
            result_attributes,
            scope="variable",
            check=Check.EXISTS,
            missing_status=_status(strict),
            on_common=lambda name: self._check_variable_attributes(
                report, rule_attributes[name], result_attributes[name], name, strict
            ),
        )

        # Rule-independent: these compare the file against a fixed convention
        for name in sorted(result.variable_attributes):
            attributes = result.variable_attributes[name]
            findings.extend(self._check_required_attributes(report, attributes, name, strict))
            for finding in (
                self._check_valid_min_max(report, attributes, name, strict),
                self._check_fill_value(
                    report,
                    attributes,
                    result.variables[name].dtype,
                    rules.fill_values,
                    name,
                    strict,
                ),
            ):
                if finding is not None:
                    findings.append(finding)

        return findings

    def _check_variable_attributes(
        self,
        report: _Reporter,
        rule: Iterable[str],
        result: Iterable[str],
        var_name: str,
        strict: bool,
    ) -> list[Finding]:
        """Compare one variable's declared attribute names against the ones the file carries.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            rule: The attribute names the SoS spec declares for this variable.
            result: The attribute names the file actually carries for this variable.
            var_name: This variable's qualified name (``group/var`` when nested).
            strict: When True, a missing attribute FAILs the run rather than WARN.

        Returns:
            One finding per attribute, qualified as ``<variable>.<attribute>``.
        """
        # Fill attributes are excluded rather than reported; value is already
        # checked by _check_fill_value
        return report.partition(
            rule,
            set(result) - set(self.FILL_ATTRS),
            scope="attribute",
            check=Check.ATTRS,
            component=lambda attribute: f"{var_name}.{attribute}",
            missing_status=_status(strict),
            parent=var_name,
        )

    def _check_required_attributes(
        self, report: _Reporter, variable: dict[str, object], var_name: str, strict: bool
    ) -> list[Finding]:
        """Check that a variable carries every attribute the SoS spec makes mandatory.

        The rule comparison cannot catch these: where the spreadsheet omits an attribute too --
        as it does for ``units`` on 69 of its 147 variables -- the name is on neither side of
        the partition, so nothing is reported.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            variable: This variable's attributes, as the file carries them.
            var_name: This variable's qualified name (``group/var`` when nested).
            strict: When True, a missing or blank attribute FAILs the run rather than WARN.

        Returns:
            One finding per required attribute that is absent or blank.
        """
        findings: list[Finding] = []

        for name in self.REQUIRED_ATTRS:
            value = variable.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                findings.append(
                    report.finding(
                        FindingType.MISSING,
                        _status(strict),
                        f"{var_name}.{name}",
                        "required by the SoS metadata spec",
                        scope="attribute",
                        check=Check.REQUIRED,
                        parent=var_name,
                    )
                )

        return findings

    def _check_valid_min_max(
        self, report: _Reporter, variable: dict[str, object], var_name: str, strict: bool
    ) -> Finding | None:
        """Check that a variable's valid_min does not exceed its valid_max.

        Bounds are coerced with :func:`_numeric` first, since netCDF reports numpy scalars and a
        few spreadsheet bounds are strings such as ``'inf'``. A bound that will not coerce is
        skipped rather than guessed at.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            variable: This variable's attributes, as the file carries them.
            var_name: This variable's qualified name (``group/var`` when nested).
            strict: When True, an inverted range FAILs the run rather than WARN.

        Returns:
            A PASSED finding when the range is ordered, a DIFFERENT finding when inverted, or
            ``None`` when either bound is absent or non-numeric.
        """
        minimum = _numeric(variable.get("valid_min"))
        maximum = _numeric(variable.get("valid_max"))

        if minimum is None or maximum is None:
            return None

        if minimum <= maximum:
            return report.finding(
                FindingType.PASSED,
                FindingStatus.INFO,
                var_name,
                scope="attribute",
                check=Check.BOUNDS,
                parent=var_name,
            )

        return report.finding(
            FindingType.DIFFERENT,
            _status(strict),
            var_name,
            f"(valid_min: {minimum}) exceeds (valid_max: {maximum})",
            scope="attribute",
            check=Check.BOUNDS,
            parent=var_name,
        )

    def _check_fill_value(
        self,
        report: _Reporter,
        variable: dict[str, object],
        dtype: str,
        fill_values: dict[str, float | int | str],
        var_name: str,
        strict: bool,
    ) -> Finding | None:
        """Check a variable's declared fill value against the canonical value for its type.

        The first of :attr:`FILL_ATTRS` present wins, and declaring none is not a violation --
        netCDF forbids ``_FillValue`` on VLEN types, so some variables use ``missing_value`` or
        the non-standard ``fill``, and some carry nothing. ``dtype`` is the variable's contract
        dtype token (``f8``, ``i4``, ``S1``, ``str``, ...); ``fill_values`` are the canonical
        values from the rules artifact, keyed by type name.

        Args:
            report: Binds the run-invariant ``Finding`` fields for this file.
            variable: This variable's attributes, as the file carries them.
            dtype: This variable's contract dtype token.
            fill_values: The canonical fill value for each type name, from the rules artifact.
            var_name: This variable's qualified name (``group/var`` when nested).
            strict: When True, a non-canonical fill value FAILs the run rather than WARN.

        Returns:
            A PASSED finding when the declared fill value is canonical, a DIFFERENT finding when
            it is not, a SKIPPED/REPORT finding when the dtype has no canonical fill value to
            check against, or ``None`` when the variable declares no fill value at all.
        """
        declared = next(
            ((name, variable[name]) for name in self.FILL_ATTRS if name in variable), None
        )
        if declared is None:
            return None

        attr_name, value = declared

        # An unmapped dtype means CIT cannot check this variable -- a gap in CIT, not the data,
        # so this is SKIPPED/REPORT (never escalated) rather than a DIFFERENT/WARN disagreement.
        fill_types = self.TOKEN_TO_FILL_TYPES.get(dtype)
        if fill_types is None:
            return report.finding(
                FindingType.SKIPPED,
                FindingStatus.REPORT,
                f"{var_name}.{attr_name}",
                f"dtype {dtype!r} has no canonical fill value; fill value not checked",
                scope="attribute",
                check=Check.FILL,
                parent=var_name,
            )

        # Indexed, not filtered: a missing key means a malformed rules artifact
        expected = [fill_values[name] for name in fill_types]
        if any(_same_fill(value, candidate) for candidate in expected):
            return report.finding(
                FindingType.PASSED,
                FindingStatus.INFO,
                f"{var_name}.{attr_name}",
                f"{value!r} is canonical for dtype {dtype!r}",
                scope="attribute",
                check=Check.FILL,
                parent=var_name,
            )

        return report.finding(
            FindingType.DIFFERENT,
            _status(strict),
            f"{var_name}.{attr_name}",
            f"(rule fill: {expected}) and (result fill: {value!r}) for dtype {dtype!r}",
            scope="attribute",
            check=Check.FILL,
            parent=var_name,
        )


def partition(
    contract: Iterable[str], result: Iterable[str]
) -> tuple[list[str], list[str], list[str]]:
    """Split two sets of component names into what is missing, extra, and common to both.

    Both directions come from the same pair of set differences, so the contract-against-result
    and result-against-contract comparisons are one operation rather than two traversals. A
    mapping may be passed for either side; iterating one yields its keys.

    Args:
        contract: The names the EXPECTED side declares (or a mapping keyed by them).
        result: The names the produced file actually holds (or a mapping keyed by them).

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
    """Return value as a float, or None when it is not numeric.

    Args:
        value: The value to coerce, e.g. a numpy scalar or a spreadsheet string.

    Returns:
        The value as a float, or ``None`` when it will not coerce.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_fill(actual: object, expected: object) -> bool:
    """Compare a declared fill value against a canonical one, across bytes, str and numeric forms.

    An ``S1`` fill reads back as ``b"x"`` while the artifact holds ``"x"``, so bytes decode first.
    The string branch must precede the numeric one, or ``"x"`` coerces to ``None`` and every char
    variable reports as mismatched.

    Args:
        actual: The fill value the produced file declares.
        expected: The canonical fill value from the rules artifact.

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
