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

:meth:`Orchestrate.parse` goes the other way, from files to a contract. Its three per-module
inputs -- the result files to describe, the SoS metadata workbook, and the workbook group tabs to
read -- arrive as three independent repeatable CLI flags, so :meth:`Orchestrate._align` matches
them on the module name each was tagged with and hands back one target per module, carrying the
:class:`~cit.parse.RulesParser` registered under that same name. Matching by name rather than by
argument order is what keeps a parse honest: the drafted contract gets committed, so its module
identity must be stated by the user, never inferred from a result filename.
"""

import functools
import logging
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from cit.contract import Contract, Produces
from cit.data import (
    find_contract_files,
    find_result_files,
    find_rules_files,
    load_yaml,
    match_result_filename,
)
from cit.parse import DEFAULT_RULE_NAME, ParseTarget, RulesParser
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

    def _by_contract(self, name: str) -> list[str]:
        """Every module whose contract declares a produced file matching ``name``.

        A contract's ``produces.filepath`` is the module's own statement of what it writes, so
        matching a filename against it is a lookup rather than a guess. It also recovers the
        modules a filename says nothing about: ``af_sword_v17_SOS_results.nc`` carries no module
        name anywhere, but it matches output's declared
        ``{continent_id}_sword_v{number}_SOS_results.nc`` exactly.

        Uses the same helper as ``validate``'s file discovery, so a template is interpreted one
        way throughout.

        Args:
            name: A bare result filename, no directory part.

        Returns:
            The matching module names, sorted; empty when no contract declares that shape.
        """
        return sorted(
            module
            for module, contract in self.contracts.items()
            if any(
                match_result_filename(produces.filepath, name) is not None
                for produces in contract.module.produces
            )
        )

    def _resolve(self, module_file: str, known: Sequence[str]) -> str:
        """Name the module that produced ``module_file``, without ever inventing a name.

        Two lookups, strongest first. A contract that declares a matching ``produces.filepath``
        wins: that is the module saying, in a committed artifact, "this file is mine". Failing
        that, the filename's trailing ``_<module>`` is matched against the groups the rules
        source declares -- which covers a module with a workbook tab but no contract yet, the
        state ``metroman`` is in today.

        Neither step reads a name off the filename freehand. Both match it against a closed,
        declared set, so a parse cannot commit a contract under a name nobody chose.

        Args:
            module_file: Path to a produced result file.
            known: The module names the second lookup may resolve to.

        Returns:
            The matched module name.

        Raises:
            ValueError: If nothing matches, or if two contracts claim the same filename.
        """
        name = Path(module_file).name
        by_contract = self._by_contract(name)
        if len(by_contract) > 1:
            raise ValueError(
                f"{name!r} matches the produces template of more than one contract "
                f"({_names(by_contract)}); name it with --module-file MODULE={module_file}"
            )
        if by_contract:
            return by_contract[0]

        stem = Path(module_file).stem
        matches = [module for module in known if stem == module or stem.endswith(f"_{module}")]
        if matches:
            return max(matches, key=len)  # 'metroman' must not lose to a shorter 'man'

        raise ValueError(
            f"cannot tell which module produced {name!r}: no contract declares a matching "
            f"produces template ({_names(sorted(self.contracts))}), and its name ends with "
            f"none of {_names(sorted(known))}. Name it explicitly with "
            f"--module-file MODULE={module_file}"
        )

    def _align(
        self,
        module_files: Sequence[tuple[str | None, str]],
        rule_file: str | None = None,
        rule_name: str = DEFAULT_RULE_NAME,
        *,
        strict: bool = False,
    ) -> list[ParseTarget]:
        """Group a parse's result files by the module that produced them.

        One rules source serves the whole parse -- the SoS metadata workbook holds a tab per
        module -- so ``--rule-file`` is a single value, and the tab a module uses is its own
        name. That leaves the module name as the only thing to establish per file, either
        stated as ``MODULE=PATH`` or matched from the filename by :meth:`_resolve`.

        Args:
            module_files: ``(module_name_or_None, path)`` per ``--module-file``.
            rule_file: The rules source (SoS metadata workbook) supplying metadata; ``None``
                drafts contracts with no SoS metadata merged in.
            rule_name: Which registered rules parser reads ``rule_file``.
            strict: When True, parsing with no rules source is an error rather than a warning.

        Returns:
            One :class:`~cit.parse.ParseTarget` per module, sorted by module name.

        Raises:
            ValueError: If no rules parser is registered as ``rule_name``, if a filename cannot
                be matched to a module, or -- under ``strict`` -- if no rules source was given.
        """
        rules = None if rule_file is None else RulesParser.create(rule_name, Path(rule_file))

        if rules is None:
            if strict:
                raise ValueError(
                    "no --rule-file; --strict requires a rules source so the drafted contracts "
                    "carry SoS metadata"
                )
            logger.warning(
                "no --rule-file; drafting from the result files alone, with no SoS metadata "
                "merged in"
            )

        known = rules.groups() if rules else sorted(self.contracts)
        grouped: dict[str, list[Path]] = {}
        for name, path in module_files:
            module = name if name is not None else self._resolve(path, known)
            grouped.setdefault(module, []).append(Path(path))

        # The rules source's own name covers the whole file it describes -- parsing `output`
        # reads every group plus the fixed tabs, so it is served even though it is not a group.
        covered = set(known) | ({rules.rule_name} if rules else set())
        uncovered = sorted(set(grouped) - covered)
        if uncovered:
            logger.warning(
                "no rules for %s; it is not one of %s, so its contract carries no SoS metadata",
                _names(uncovered),
                _names(sorted(covered)),
            )

        return [
            ParseTarget(
                module=module,
                module_files=tuple(grouped[module]),
                rules=rules if module in covered else None,
            )
            for module in sorted(grouped)
        ]

    def parse(
        self,
        module_files: Sequence[tuple[str | None, str]],
        rule_file: str | None = None,
        rule_name: str = DEFAULT_RULE_NAME,
        *,
        strict: bool = False,
    ) -> list[ParseTarget]:
        """Resolve a parse into one target per module: its result files and its rules parser.

        Resolving the modules and choosing the rules parser is the whole of this operation
        today; drafting each target's contract lands with ``ContractParser.parse`` in P1-10.

        Args:
            module_files: ``(module_name_or_None, path)`` per ``--module-file``.
            rule_file: The rules source supplying SoS metadata, or None.
            rule_name: Which registered rules parser reads ``rule_file``.
            strict: When True, parsing with no rules source is an error.

        Returns:
            One :class:`~cit.parse.ParseTarget` per module, sorted by module name.

        Raises:
            ValueError: If the parse cannot be resolved (see :meth:`_align`).
        """
        return self._align(module_files, rule_file, rule_name, strict=strict)


def _names(names: Sequence[str]) -> str:
    """Render module names for an error message.

    Args:
        names: The module names to render.

    Returns:
        The names comma-separated and quoted, or ``"none"`` when empty.
    """
    return ", ".join(repr(name) for name in names) or "none"
