""""""

import functools
from collections.abc import Iterable, Iterator

from cit.data import find_contract_files, find_result_files, find_rules_files, load_yaml
from cit.models import Contract
from cit.report import Finding, Report
from cit.result import NetcdfResult


class Orchestrate:
    """"""

    def __init__(self, data_mount: str) -> None:
        """"""
        self._data_mount = data_mount

    @functools.cached_property
    def contracts(self) -> dict[str, Contract]:
        """"""
        contract_files = find_contract_files()

        contracts = {}
        for contract_file in contract_files:
            contract = Contract.model_validate(load_yaml(contract_file))
            contracts[contract.ModuleContract.name] = contract

        return contracts

    @functools.cached_property
    def rules(self) -> dict:
        """"""
        rules_files = find_rules_files()
        return {}

    def iter_results(self, module: str) -> Iterator[NetcdfResult]:
        """Lazily yield one NetcdfResult per produced file for `module` under `path`.

        Nothing is read until a result's property is accessed; the caller scopes each with `with`
        so only one file is resident at a time. The result carries its own `.filepath`.
        """
        contract = self.contracts[module]
        for produces in contract.module.produces:
            for filepath in find_result_files(self._data_mount, produces.filepath):
                yield NetcdfResult(str(filepath))

    def validate(self, module: str, path: str, strict: bool = False) -> list[Finding]:
        """Validate one module's produced results against its contract, one file at a time."""
        contract = self.contracts[module]
        findings: list[Finding] = []
        for result in self.iter_results(module, path):          # note: pass `path`
            with result:
                # findings += compare_contract_results(contract, result)   # P1-6
                # findings += validate_rules(self.rules, result, strict)   # P1-15
                pass
        return findings

    def run(self, path: str, strict: bool = False, modules: Iterable[str] | None = None) -> Report:
        """Validate every module (or a subset) under `path` and aggregate a single Report."""
        modules = modules or self.contracts.keys()
        findings = [f for m in modules for f in self.validate(m, path, strict)]
        return Report(findings)