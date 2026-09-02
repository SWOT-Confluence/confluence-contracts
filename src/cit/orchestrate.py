"""Orchestrator: own the bundled resources, then drive each ``cit`` operation over them.

``Orchestrate`` is the single layer between the CLI and the domain models. It loads the bundled
resources -- contracts (EXPECTED side) and SoS metadata-rules artifacts -- once, caching each, and
exposes one method per ``cit`` subcommand that works from them. Resource loading lives here rather
than in :mod:`cit.__main__` so that "which contract/rules artifact applies to this module" is
answered in exactly one place, testable without argparse.

:meth:`Orchestrate.validate` discovers a module's produced files via :mod:`cit.data` and streams
them through :class:`cit.result.NetcdfResult` (ACTUAL side) lazily -- opening, checking, and closing
one file at a time so peak memory stays at a single result regardless of how many were produced --
then aggregates every module's findings into a single :class:`~cit.report.Report`, passing along
:attr:`contracts` so the rendered banner can show each module's version, branch, and commit.

The run mount is an argument to :meth:`validate`, not constructor state: it is specific to that one
operation -- ``cit parse`` reads designated files rather than a mount tree.

:meth:`Orchestrate.parse` goes the other way, from files to contracts and rules artifacts. It
receives module files and rules sources already grouped by name (``MODULE=PATH`` tags from the
CLI), builds one :class:`~cit.parse.ContractParser` per module and one
:class:`~cit.parse.RulesParser` per rules source, and returns a :class:`~cit.parse.ParsePlan`
that records where the two sets intersect. Matching by name rather than by argument order is
what keeps a parse honest: the drafted contract gets committed, so its module identity is always
something the user typed, never inferred from a result filename.
"""

import functools
import logging
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

from cit.contract import Contract, Produces
from cit.data import (
    find_contract_files,
    find_result_files,
    find_rules_files,
    load_yaml,
)
from cit.parse import ContractParser, ParsePlan, RulesParser
from cit.report import DEFAULT_MAX_FILES, Finding, Report, ValidationSource
from cit.result import NetcdfResult
from cit.rules import MetadataRules
from cit.validation import Validator, ValidatorContext

logger = logging.getLogger(__name__)   # "cit.orchestrate" — child of "cit"


class Orchestrate:
    """Own the bundled contracts and rules artifacts, and drive each ``cit`` operation over them."""

    @functools.cached_property
    def _validators(self) -> list[Validator]:
        """The discovered structural/metadata validators (cached; only paid on a validate).

        A ``cached_property`` rather than constructor state so that operations which do not
        validate anything -- ``cit parse`` (P1-10) -- never trigger the discovery walk.

        Returns:
            Every :class:`~cit.validation.Validator` subclass discovered in :mod:`cit.validation`.
        """
        return Validator.discover()

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

    def iter_results(self, module: str, data_mount: str) -> Iterator[tuple[Produces, NetcdfResult]]:
        """Lazily yield one :class:`NetcdfResult` per produced file for ``module``.

        Nothing is read until a result's property is accessed; the caller scopes each with ``with``
        so only one file is resident at a time. Each result carries its own ``.filepath``.

        Args:
            module: The module whose produced files to stream (a key of :attr:`contracts`).
            data_mount: Path to the run mount holding the results tree (e.g. containing
                ``flpe/momma/``).

        Yields:
            One :class:`NetcdfResult` per file matching the module's ``Produces.filepath`` template
            under the run mount.
        """
        contract = self.contracts[module]
        for produces in contract.module.produces:
            for filepath in find_result_files(data_mount, produces.filepath):
                yield produces, NetcdfResult(str(filepath))

    def _validate_module(
        self, module: str, data_mount: str, strict: bool = False
    ) -> list[Finding]:
        """Validate one module's produced results against its contract, one file at a time.

        Args:
            module: The module to validate (a key of :attr:`contracts`).
            data_mount: Path to the run mount holding the results tree.
            strict: When True, treat SoS metadata-rule violations as failures rather than warnings.

        Returns:
            The findings for this module, across every discovered validator.
        """
        findings: list[Finding] = []
        rules = self.rules.get(module)  # None when this module has no rules artifact
        for produces, result in self.iter_results(module, data_mount):
            with result:
                ctx = ValidatorContext(module, produces, rules, result, strict)
                for validator in self._validators:
                    findings.extend(validator.validate(ctx))
        return findings

    def validate(
        self,
        data_mount: str,
        *,
        strict: bool = False,
        modules: Iterable[str] | None = None,
        show_passed: bool = False,
        show_files: bool = False,
        max_files: int = DEFAULT_MAX_FILES,
        checks: ValidationSource | None = None,
    ) -> Report:
        """Validate every module (or a given subset) and aggregate a single report.

        Args:
            data_mount: Path to the run mount holding the results tree (e.g. containing
                ``flpe/momma/``).
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
        findings = [
            finding
            for module in modules
            for finding in self._validate_module(module, data_mount, strict)
        ]
        contracts = {name: self.contracts[name] for name in modules}
        return Report(
            findings,
            contracts,
            show_passed=show_passed,
            show_files=show_files,
            max_files=max_files,
            checks=checks,
        )

    def parse(
        self,
        version: str,
        repo_config: dict[str, str],
        module_files: Mapping[str, str],
        rule_files: Mapping[str, str] | None = None,
        *,
        strict: bool = False,
    ) -> ParsePlan:
        """Build a :class:`~cit.parse.ParsePlan` from tagged module files and rules sources.

        Creates one :class:`~cit.parse.ContractParser` per module key and one
        :class:`~cit.parse.RulesParser` per rules key. Parser ``parse()`` bodies are not
        invoked; drafting each contract and rules artifact happens in the next step (P1-10).

        Args:
            module_files: Produced result files keyed by module name -- each key is a module name
                and each value is the single exemplar result file path for that module's contract
                parser.
            rule_files: Rules sources keyed by the registered rules-parser name (e.g.
                ``"output"``); ``None`` drafts contracts with no SoS metadata merged in.
            strict: When True, parsing with no rules source is an error rather than a warning.

        Returns:
            A :class:`~cit.parse.ParsePlan` carrying the constructed parsers and the
            :attr:`~cit.parse.ParsePlan.both` intersection.

        Raises:
            ValueError: If a rules name matches no registered parser (see
                :meth:`~cit.parse.RulesParser.create`), or -- under ``strict`` -- if no
                ``rule_files`` were given.
        """
        # Build rules parsers first so a bad rule name raises before any contract parsers are built.
        rules: dict[str, RulesParser] = {}
        if rule_files:
            for name, path in rule_files.items():
                rules[name] = RulesParser.create(name, Path(path))

        if not rules:
            if strict:
                raise ValueError(
                    "no --rule-file; --strict requires a rules source so the drafted contracts "
                    "carry SoS metadata"
                )
            logger.warning(
                "no --rule-file; drafting from the result files alone, with no SoS metadata "
                "merged in"
            )

        contracts: dict[str, ContractParser] = {
            module: ContractParser(module, Path(path), Path(repo_config[module]), version)
            for module, path in module_files.items()
        }

        plan = ParsePlan(contracts=contracts, rules=rules)

        for module_name, contract in plan.contracts.items():
            contract.parse()

        # # Log the three-line summary so a library caller and the CLI see the same record.
        # logger.info("contracts: %s", ", ".join(f"'{m}'" for m in sorted(contracts)) or "none")
        # logger.info(
        #     "rules:     %s",
        #     ", ".join(f"'{n}' ({type(r).__name__})" for n, r in sorted(rules.items())) or "none",
        # )
        # logger.info("both:      %s", ", ".join(f"'{m}'" for m in plan.both) or "none")
