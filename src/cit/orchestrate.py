"""Streaming orchestrator: load contracts, then validate produced results one file at a time.

``Orchestrate`` ties the pieces together for a validation run. It loads the bundled contracts
(EXPECTED side) once, discovers a module's produced files via :mod:`cit.data`, and streams them
through :class:`cit.result.NetcdfResult` (ACTUAL side) lazily -- opening, checking, and closing one
file at a time so peak memory stays at a single result regardless of how many were produced.

The per-file comparison (``validate``) and the aggregate ``run`` are included for context; the
validators they call (``ResultsValidation`` P1-6, ``RulesValidation`` P1-15) and ``Report`` (P1-8)
land in later issues, so for now they stream but produce no findings.
"""

import functools
from collections.abc import Iterable, Iterator

from cit.data import find_contract_files, find_result_files, find_rules_files, load_yaml
from cit.models import Contract
from cit.report import Finding, Report
from cit.result import NetcdfResult


class Orchestrate:
    """Drive a validation run: load contracts and stream produced results against them."""

    def __init__(self, data_mount: str) -> None:
        """Store the run mount root; nothing is loaded until a property is accessed.

        Args:
            data_mount: Path to the run mount holding the results tree (e.g. containing
                ``flpe/momma/``).
        """
        self._data_mount = data_mount

    @functools.cached_property
    def contracts(self) -> dict[str, Contract]:
        """The bundled contracts, keyed by module name (loaded and validated once, then cached)."""
        contracts: dict[str, Contract] = {}
        for contract_file in find_contract_files():
            contract = Contract.model_validate(load_yaml(contract_file))
            contracts[contract.module.name] = contract
        return contracts

    @functools.cached_property
    def rules(self) -> dict:
        """The SoS metadata rules -- stubbed (empty) until ``RulesValidation`` lands in P1-15."""
        rules_files = find_rules_files()  # noqa: F841  (stub: rules parsing arrives in P1-15)
        return {}

    def iter_results(self, module: str) -> Iterator[NetcdfResult]:
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
                yield NetcdfResult(str(filepath))

    def validate(self, module: str, strict: bool = False) -> list[Finding]:
        """Validate one module's produced results against its contract, one file at a time.

        Args:
            module: The module to validate (a key of :attr:`contracts`).
            strict: When True, treat rule violations as failures (used by the P1-15 rules check).

        Returns:
            The findings for this module (empty until the validators land in P1-6/P1-15).
        """
        contract = self.contracts[module]  # noqa: F841  (used by the P1-6 comparison, stubbed below)
        findings: list[Finding] = []
        for result in self.iter_results(module):
            with result:
                # findings += compare_contract_results(contract, result)   # P1-6
                # findings += validate_rules(self.rules, result, strict)   # P1-15
                pass
        return findings

    def run(self, strict: bool = False, modules: Iterable[str] | None = None) -> Report:
        """Validate every module (or a given subset) and aggregate a single report.

        Args:
            strict: When True, rule violations fail the run.
            modules: Modules to validate; defaults to every loaded contract.

        Returns:
            A :class:`Report` aggregating the findings across all validated modules.
        """
        modules = modules or self.contracts.keys()
        findings = [finding for module in modules for finding in self.validate(module, strict)]
        return Report(findings)
