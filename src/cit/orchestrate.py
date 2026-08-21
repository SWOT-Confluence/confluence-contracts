"""Streaming orchestrator: load contracts, then validate produced results one file at a time.

``Orchestrate`` ties the pieces together for a validation run. It loads the bundled contracts
(EXPECTED side) once, discovers a module's produced files via :mod:`cit.data`, and streams them
through :class:`cit.result.NetcdfResult` (ACTUAL side) lazily -- opening, checking, and closing one
file at a time so peak memory stays at a single result regardless of how many were produced.

``run`` aggregates every module's findings into a single :class:`~cit.report.Report`, passing
along :attr:`contracts` so the rendered report's banner can show each module's version, branch,
and commit.
"""

import functools
from collections.abc import Iterable, Iterator

from cit.contract import Contract, Produces
from cit.data import find_contract_files, find_result_files, find_rules_files, load_yaml
from cit.report import DEFAULT_MAX_FILES, Finding, Report, ValidationSource
from cit.result import NetcdfResult
from cit.rules import MetadataRules
from cit.validation import Validator, ValidatorContext


class Orchestrate:
    """Drive a validation run: load contracts and stream produced results against them."""

    def __init__(self, data_mount: str) -> None:
        """Store the run mount root; nothing is loaded until a property is accessed.

        Args:
            data_mount: Path to the run mount holding the results tree (e.g. containing
                ``flpe/momma/``).
        """
        self._data_mount = data_mount
        self._validators = Validator.discover()

    @functools.cached_property
    def contracts(self) -> dict[str, Contract]:
        """The bundled contracts, keyed by module name (loaded and validated once, then cached).

        Returns:
            A mapping of module name to its loaded :class:`Contract`.
        """
        contracts: dict[str, Contract] = {}
        for contract_file in find_contract_files():
            contract = Contract.model_validate(load_yaml(contract_file))
            contracts[contract.module.name] = contract
        return contracts

    @functools.cached_property
    def rules(self) -> dict[str, MetadataRules]:
        """The SoS metadata rules artifacts, keyed by the module name they govern.

        Returns:
            A mapping of module name to its loaded :class:`MetadataRules`, empty for a module
            with no rules artifact.
        """
        rules: dict[str, MetadataRules] = {}
        for rules_file in find_rules_files():
            metadata_rules = MetadataRules.model_validate(load_yaml(rules_file))
            rules[metadata_rules.module_name] = metadata_rules
        return rules

    def iter_results(self, module: str) -> Iterator[tuple[Produces, NetcdfResult]]:
        """Lazily yield one :class:`NetcdfResult` per produced file for ``module``.

        Nothing is read until a result's property is accessed; the caller scopes each with ``with``
        so only one file is resident at a time. Each result carries its own ``.filepath``.

        Args:
            module: The module whose produced files to stream (a key of :attr:`contracts`).

        Yields:
            One :class:`NetcdfResult` per file matching the module's ``Produces.filepath`` template
            under the run mount.
        """
        contract = self.contracts[module]
        for produces in contract.module.produces:
            for filepath in find_result_files(self._data_mount, produces.filepath):
                yield produces, NetcdfResult(str(filepath))

    def validate(self, module: str, strict: bool = False) -> list[Finding]:
        """Validate one module's produced results against its contract, one file at a time.

        Args:
            module: The module to validate (a key of :attr:`contracts`).
            strict: When True, treat SoS metadata-rule violations as failures rather than warnings.

        Returns:
            The findings for this module, across every discovered validator.
        """
        findings: list[Finding] = []
        rules = self.rules.get(module)  # None when this module has no rules artifact
        for produces, result in self.iter_results(module):
            with result:
                ctx = ValidatorContext(module, produces, rules, result, strict)
                for validator in self._validators:
                    findings.extend(validator.validate(ctx))
        return findings

    def run(
        self,
        strict: bool = False,
        modules: Iterable[str] | None = None,
        *,
        show_passed: bool = False,
        show_files: bool = False,
        max_files: int = DEFAULT_MAX_FILES,
        checks: ValidationSource | None = None,
    ) -> Report:
        """Validate every module (or a given subset) and aggregate a single report.

        Args:
            strict: When True, rule violations fail the run.
            modules: Modules to validate; defaults to every loaded contract.
            show_passed: When True, the rendered report also shows components whose findings
                are all PASSED (see :class:`cit.report.Report`).
            show_files: When True, the rendered report also lists the result-file basenames
                behind a multi-file finding (see :class:`cit.report.Report`).
            max_files: How many basenames to list per finding when ``show_files`` is set.
            checks: Restrict the rendered report to one :class:`~cit.report.ValidationSource`'s
                section (see :class:`cit.report.Report`); passed straight through unvalidated.

        Returns:
            A :class:`Report` aggregating the findings across all validated modules, carrying
            :attr:`contracts` so its banner can show each module's version/branch/commit.
        """
        modules = list(modules) if modules is not None else list(self.contracts.keys())
        findings = [finding for module in modules for finding in self.validate(module, strict)]
        contracts = {name: self.contracts[name] for name in modules}
        return Report(
            findings,
            contracts,
            show_passed=show_passed,
            show_files=show_files,
            max_files=max_files,
            checks=checks,
        )
