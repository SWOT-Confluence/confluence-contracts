"""Findings and reporting: the shared output vocabulary for every check.

Defines the single ``Finding`` type that all validators emit and the ``Report`` that
aggregates them -- kept separate from the ``Result`` read model so "a validator's output"
and "an actual file" are never confused.

``Finding`` and its enums have landed: a finding pairs a ``FindingType`` (what was found) with a
``FindingStatus`` (how it bears on the exit code), so severity can be re-weighted -- e.g. by
``--strict`` -- without changing what a validator reports.

``Report`` aggregates findings, deduplicates them for display (see :meth:`Report.deduplicated`),
groups them along an arbitrary axis (see :meth:`Report.grouped_by`), and applies the exit policy
(see :attr:`Report.exit_code`) -- any ``FAIL`` fails the run, a ``WARN``-only or empty run
passes, and ``REPORT`` findings never affect the exit code, even under ``--strict``.

``Report.__str__`` (P1-9.3) renders that aggregation as text: a version banner, a legend, the
summary counts line, and the findings themselves grouped **component-first** -- module, then
produced-file template, then component, with each component's findings ordered FAIL -> WARN ->
PASS. Grouping is component-first rather than severity-first (superseding the original plan) so
that one variable's disagreeing and agreeing checks land together instead of scattered across
severity sections -- see GitHub issue #10's first comment. Components are sorted by their worst
severity, so a FAIL-bearing component still sorts before a WARN-only one; by default a component
is shown only if at least one of its findings is not PASSED, and then *all* of its findings
(including the PASSED ones) render together -- that adjacency is the point of the change.
``show_passed`` additionally reveals components whose findings are all PASSED.

:meth:`Report.write_csv` (P1-9.4) exports every raw finding -- one row per occurrence, no
deduplication -- as the triage escape hatch for the file-level detail the deduplicated text
report deliberately drops.

Planned (P1-9, remaining):

- CLI wiring (9.5): ``cit validate``/``cit parse`` and the locked ``print(report);
  sys.exit(report.exit_code)`` shape.
"""

import csv
import importlib.metadata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from pathlib import Path

from cit.contract import Contract

_DISTRIBUTION_NAME = "confluence-contracts"

_LEGEND = """\
Declared = what the contract (structure) or the SoS rules (metadata) say should be there.
Found    = what the produced file actually holds.

PASSED     Declared and found, contract/rules match module file.
MISSING    Declared, but not found in the file. Data is missing from the module file.
EXTRA      Found in the file, but not declared. Extra data located in the module file.
DIFFERENT  Declared and found, contract/rules do not match module file. Message indicates
           values for both."""

_CHECK_WIDTH = len("global_attribute")
_TYPE_WIDTH = len("DIFFERENT")


def _cit_version() -> str:
    """Return the installed ``cit`` version, or a placeholder outside an installed package.

    Returns:
        The ``confluence-contracts`` distribution version, or ``"0+unknown"`` if package
        metadata cannot be found (e.g. running from a source checkout with no install record).
    """
    try:
        return importlib.metadata.version(_DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


class FindingType(StrEnum):
    """What a check found, independent of how severely the run should treat it.

    Attributes:
        MISSING: The contract declares a component the result file does not contain.
        EXTRA: The result file contains a component the contract does not declare (drift).
        DIFFERENT: The component exists on both sides but its structure disagrees.
        PASSED: The component exists on both sides and every structural check agreed.
    """

    MISSING = "MISSING"
    EXTRA = "EXTRA"
    DIFFERENT = "DIFFERENT"
    PASSED = "PASSED"


class FindingStatus(StrEnum):
    """How a finding bears on the run's exit policy.

    Attributes:
        FAIL: A broken interface guarantee; fails the run.
        WARN: Drift or an absent optional component; reported without failing.
        INFO: A check that agreed; carried so a report can show what was verified.
        REPORT: A report-only observation (e.g. a P1-16 health check) that is always shown but
            never affects the exit code, even under ``--strict``.
    """

    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"
    REPORT = "REPORT"


_SEVERITY = {
    FindingStatus.FAIL: 0,
    FindingStatus.WARN: 1,
    FindingStatus.INFO: 2,
    # REPORT sorts last: it is not part of the pass/fail/warn ladder at all -- it never affects
    # the exit code and, when a group of findings is ranked by its worst severity, a report-only
    # note must never outrank (or masquerade as) a real pass/fail/warn finding.
    FindingStatus.REPORT: 3,
}


@dataclass(frozen=True)
class Finding:
    """One check outcome emitted by a validator.

    Frozen so a finding cannot be edited after a validator emits it, and hashable so a report
    may group or dedupe findings (see :class:`Report`).

    Attributes:
        type: What the check found (see :class:`FindingType`).
        status: How the finding bears on the exit policy (see :class:`FindingStatus`).
        module_name: The module whose contract was checked (e.g. ``momma``).
        component: The dimension or variable this finding is about.
        filepath: The contract's declared path template for the produced file (e.g.
            ``flpe/momma/{reach_id}_momma.nc``), not the resolved file on disk.
        validation: Which validation produced the finding (``contract`` or ``rule``), so a report
            can group by check and ``--strict`` can escalate only rule findings.
        message: Optional detail, e.g. the disagreeing contract and result values.
        results_file: The resolved produced file the finding came from (e.g.
            ``flpe/momma/74267700071_momma.nc``), distinct from ``filepath``'s path template.
        check: What kind of thing was examined -- ``dimension``, ``variable``, ``attribute`` or
            ``global_attribute`` -- a different axis from ``validation`` (contract vs rule). Has
            no default: every finding must name what it checked, so a missing value fails loudly
            at construction rather than silently grouping unlike findings together.
    """

    type: FindingType
    status: FindingStatus
    module_name: str
    component: str
    filepath: str
    validation: str
    message: str = ""
    results_file: str = ""
    check: str = field(kw_only=True)


def _sort_key(finding: Finding) -> tuple[int, str, str, str, str, str]:
    """Deterministic ordering key: severity first, then a stable tiebreaker.

    Used both to order the findings within one group and to order deduplicated entries, so
    rendering never depends on dict/set iteration order.

    Args:
        finding: The finding to compute an ordering key for.

    Returns:
        A tuple ordering by severity (:data:`_SEVERITY`), then module, component, check, message
        and finally the contract's filepath template.
    """
    return (
        _SEVERITY[finding.status],
        finding.module_name,
        finding.component,
        finding.check,
        finding.message,
        finding.filepath,
    )


@dataclass(frozen=True)
class DedupedFinding:
    """One distinct finding after collapsing duplicates that differ only by ``results_file``.

    Attributes:
        finding: A representative finding for this entry, with ``results_file`` cleared to
            ``""`` -- it is not meaningful for a deduplicated entry, use ``files`` instead.
        count: How many raw findings collapsed into this entry.
        files: The distinct ``results_file`` values the finding was seen in, sorted for
            deterministic rendering (e.g. name the one file, or report ``len(files)`` many).
    """

    finding: Finding
    count: int
    files: tuple[str, ...]


class Report:
    """Aggregate findings, dedupe/group them for display, and apply the run's exit policy."""

    def __init__(
        self,
        findings: list[Finding],
        contracts: dict[str, Contract] | None = None,
        *,
        show_passed: bool = False,
    ) -> None:
        """Store the findings to aggregate, plus what ``__str__`` needs to render them.

        ``contracts`` and ``show_passed`` are both optional so existing ``Report(findings)``
        callers (including every test written before P1-9.3) keep working unchanged; without
        them ``__str__`` still renders, just with a degraded banner and PASSED-only components
        hidden.

        Args:
            findings: The check outcomes collected across a validation run.
            contracts: The contracts loaded for this run, keyed by module name (e.g.
                ``Orchestrate.contracts``), used to print each module's version/branch/commit in
                the banner. ``None`` renders a banner with no per-module version data.
            show_passed: When True, ``__str__`` also renders components whose findings are all
                PASSED, not just the ones with at least one non-PASSED finding.
        """
        self._findings = findings
        self._contracts = contracts or {}
        self._show_passed = show_passed

    @property
    def findings(self) -> list[Finding]:
        """The raw, undeduplicated findings collected across the run.

        Kept accessible (not just internal) so a full export -- e.g. the P1-9.4 CSV, which needs
        every occurrence rather than one deduplicated entry -- can still get at each one.
        """
        return list(self._findings)

    @property
    def exit_code(self) -> int:
        """The run's exit code: 1 if any finding is FAIL, else 0.

        REPORT findings are excluded from consideration entirely -- they never affect the exit
        code, not even under ``--strict``.

        Report does not re-escalate anything itself: ``--strict`` promotion of rule WARNs to
        FAILs already happens at emit time in ``validation._status``, which keeps a finding's
        status truthful at the point it was produced. Report only consumes the status it is
        given. Measured asymmetry from a real mount run: ``--strict`` moved 171 rule findings
        WARN -> FAIL and left 92 untouched, because ``partition`` hardcodes ``EXTRA`` to WARN --
        an extra attribute is drift, not a violation, and staying WARN under ``--strict`` reflects
        that.
        """
        return 1 if any(finding.status is FindingStatus.FAIL for finding in self._findings) else 0

    def deduplicated(self) -> list[DedupedFinding]:
        """Collapse findings that differ only by ``results_file`` into one entry each.

        At mount scale the same findings recur once per produced file (e.g. the same 39 momma
        findings printed 19 times, once per reach file); deduplicating on every field except
        ``results_file`` turns that repetition into one entry annotated with how many files --
        and which ones -- it was seen in.

        Returns:
            One :class:`DedupedFinding` per distinct finding (all fields but ``results_file``),
            sorted deterministically by :func:`_sort_key`.
        """
        entries: dict[Finding, tuple[int, list[str]]] = {}
        for finding in self._findings:
            key = replace(finding, results_file="")
            count, files = entries.get(key, (0, []))
            files.append(finding.results_file)
            entries[key] = (count + 1, files)
        deduped = [
            DedupedFinding(finding=key, count=count, files=tuple(sorted(set(files))))
            for key, (count, files) in entries.items()
        ]
        return sorted(deduped, key=lambda entry: _sort_key(entry.finding))

    def grouped_by(self, key: Callable[[Finding], str]) -> dict[str, list[Finding]]:
        """Group findings by an arbitrary key, in a deterministic order.

        One generic helper rather than a method per axis: rendering already needs three axes
        (module, file template, component) and a fourth (P1-16) is expected, so the grouping
        logic itself should not need to grow.

        Args:
            key: Maps a finding to the string it should be grouped under (e.g. its module name,
                file template, or component).

        Returns:
            A dict from group key to its findings. Both the dict's key order (alphabetical) and
            each group's finding order (by :func:`_sort_key`) are sorted so rendering never
            depends on dict/set iteration order.
        """
        groups: dict[str, list[Finding]] = {}
        for finding in self._findings:
            groups.setdefault(key(finding), []).append(finding)
        return {
            group_key: sorted(groups[group_key], key=_sort_key) for group_key in sorted(groups)
        }

    def write_csv(self, path: str | Path) -> None:
        """Write every raw finding to ``path`` as CSV, one row per occurrence.

        Unlike ``__str__`` (deduplicated for readability) or :meth:`deduplicated`, this is the
        un-deduplicated escape hatch for triage: every ``results_file`` a finding was seen in
        gets its own row, so the odd file out is recoverable in a spreadsheet. Uses only the
        stdlib ``csv`` module -- pandas/rich/tabulate are not runtime dependencies of CIT.

        Args:
            path: Where to write the CSV file.
        """
        fieldnames = [f.name for f in fields(Finding)]
        rows = sorted(self._findings, key=_sort_key)
        with Path(path).open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for finding in rows:
                writer.writerow(
                    {name: str(getattr(finding, name)) for name in fieldnames}
                )

    def __str__(self) -> str:
        """Render the banner, legend, counts line, and component-grouped findings.

        The locked API is ``print(report)``, so rendering lives on ``__str__`` rather than a
        separate ``render()`` method; anything that varies rendering (``show_passed``) is
        therefore a constructor argument instead of a call-time one.

        Returns:
            The full report text, deterministic for a given set of findings/contracts/
            ``show_passed`` -- byte-identical across runs on the same inputs.
        """
        sections = [
            self._banner(),
            "",
            _LEGEND,
            "",
            self._counts_line(),
            "",
        ]
        body = self._render_findings()
        sections.append(body if body else "(no findings)")
        return "\n".join(sections)

    def _banner(self) -> str:
        """Build the one-line banner: cit's version, then each module's version/branch/commit.

        Returns:
            ``cit <version>`` alone when no contracts were supplied, otherwise that plus one
            ``<module> <version> @ <branch> <commit>`` segment per module, sorted by name,
            joined with a middle-dot separator.
        """
        segments = [f"cit {_cit_version()}"]
        for module_name in sorted(self._contracts):
            contract = self._contracts[module_name]
            segments.append(
                f"{module_name}  {contract.version} @ "
                f"{contract.source.branch} {contract.source.commit}"
            )
        return "  ·  ".join(segments)

    def _counts_line(self) -> str:
        """Build the summary counts line: the only at-a-glance severity total in the report.

        Counts are taken from the deduplicated findings (what the reader actually sees), while
        the file/module tallies are taken from the raw findings (what actually ran).

        Returns:
            E.g. ``FAIL 58   WARN 428   PASS 632   1118 findings over 25 files, 2 modules``.
        """
        deduped = self.deduplicated()
        counts = Counter(entry.finding.status for entry in deduped)
        parts = [
            f"FAIL {counts.get(FindingStatus.FAIL, 0)}",
            f"WARN {counts.get(FindingStatus.WARN, 0)}",
            f"PASS {counts.get(FindingStatus.INFO, 0)}",
        ]
        if counts.get(FindingStatus.REPORT, 0):
            parts.append(f"REPORT {counts[FindingStatus.REPORT]}")

        files = {finding.results_file for finding in self._findings if finding.results_file}
        modules = {finding.module_name for finding in self._findings}
        return (
            "   ".join(parts)
            + f"   {len(deduped)} findings over {len(files)} files, {len(modules)} modules"
        )

    def _render_findings(self) -> str:
        """Render the findings, grouped module -> produced-file template -> component.

        Deduplicated entries are used throughout, so each rendered line stands for however many
        raw occurrences it collapsed (see :meth:`deduplicated`). Components are ranked by their
        worst severity so a FAIL-bearing component sorts before a WARN-only one, then by name;
        by default only components with at least one non-PASSED finding are shown, but every
        finding for a shown component (PASSED included) renders together.

        Returns:
            The rendered findings, or ``""`` if there is nothing to show.
        """
        deduped = self.deduplicated()
        if not deduped:
            return ""

        by_finding = {entry.finding: entry for entry in deduped}
        representatives = [entry.finding for entry in deduped]
        # Reuse grouped_by (not a bespoke method per axis) by wrapping each axis's slice in a
        # throwaway Report -- grouping logic lives in exactly one place regardless of how many
        # axes rendering nests through.
        module_groups = Report(representatives).grouped_by(lambda finding: finding.module_name)

        lines: list[str] = []
        for module_name, module_findings in module_groups.items():
            lines.append(module_name)
            file_groups = Report(module_findings).grouped_by(lambda finding: finding.filepath)
            for filepath, file_findings in file_groups.items():
                lines.append(f"  {filepath}")
                lines.extend(self._render_components(file_findings, by_finding))
        return "\n".join(lines)

    def _render_components(
        self, findings: list[Finding], by_finding: dict[Finding, DedupedFinding]
    ) -> list[str]:
        """Render one produced file's components, ordered by worst severity then name.

        Args:
            findings: The deduplicated, representative findings for one module/filepath group.
            by_finding: Maps a representative finding back to its :class:`DedupedFinding` (for
                the occurrence count and file list a plain ``Finding`` no longer carries).

        Returns:
            One heading line plus one (or two, if there is a message) lines per finding, for
            every component that is shown -- skipping PASSED-only components unless
            ``show_passed`` was set.
        """
        component_groups = Report(findings).grouped_by(lambda finding: finding.component)
        ordered = sorted(
            component_groups.items(),
            key=lambda item: (min(_SEVERITY[f.status] for f in item[1]), item[0]),
        )

        lines: list[str] = []
        for component, component_findings in ordered:
            all_passed = all(f.type == FindingType.PASSED for f in component_findings)
            if all_passed and not self._show_passed:
                continue
            lines.append(f"    {component}")
            for finding in component_findings:
                entry = by_finding[finding]
                line = (
                    f"      {finding.check:<{_CHECK_WIDTH}} {finding.type:<{_TYPE_WIDTH}} "
                    f"{_files_suffix(entry)}"
                )
                lines.append(line.rstrip())
                if finding.message:
                    lines.append(f"      {' ' * (_CHECK_WIDTH + _TYPE_WIDTH + 1)}{finding.message}")
        return lines


def _files_suffix(entry: DedupedFinding) -> str:
    """Describe how many distinct results files a deduplicated finding was seen in.

    Tolerates a finding with no file at all (e.g. P1-17's registry cross-check, which has
    nothing on disk behind it): if every ``entry.files`` value is empty, no suffix is rendered.

    Args:
        entry: The deduplicated finding to describe.

    Returns:
        ``"x<n> file(s)"`` for the distinct non-empty ``results_file`` values seen, or ``""``.
    """
    real_files = [f for f in entry.files if f]
    if not real_files:
        return ""
    noun = "file" if len(real_files) == 1 else "files"
    return f"x{len(real_files)} {noun}"
