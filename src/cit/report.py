"""Findings and reporting: the shared output vocabulary for every check.

Defines the single ``Finding`` type that all validators emit and the ``Report`` that
aggregates them -- kept separate from the ``Result`` read model so "a validator's output"
and "an actual file" are never confused.

A finding has five meaningful axes, and every one of them renders: ``validation`` (which
validator produced it -- :class:`ValidationSource`), ``scope`` (the kind of thing examined --
dimension, variable, attribute or global attribute), ``check`` (the specific question asked --
:class:`Check`), ``type`` (what was found -- :class:`FindingType`) and ``status`` (how severely
the run should treat it -- :class:`FindingStatus`). Modelling all five, instead of collapsing
several into one overloaded ``check`` string, is what lets two lines that used to render
byte-identical (e.g. a contract's "this variable is undeclared" and the SoS rules' "this
variable has no metadata spec", or a FAIL sitting next to a WARN) read as the different checks
they are.

``Report`` aggregates findings, deduplicates them for display (see :attr:`Report.deduplicated`),
groups them along an arbitrary axis (see :meth:`Report.grouped_by`), and applies the exit policy
(see :attr:`Report.exit_code`) -- any ``FAIL`` fails the run, a ``WARN``-only or empty run
passes, and ``REPORT`` findings never affect the exit code, even under ``--strict``.

``Report.__str__`` (P1-9.3, extended by #10) renders that aggregation as text: a version banner,
a legend, the summary counts line, and the findings themselves grouped **component-first** --
module, then produced-file template, then component, with each component's findings ordered
FAIL -> WARN -> PASS. Grouping is component-first rather than severity-first (superseding the
original plan) so that one variable's disagreeing and agreeing checks land together instead of
scattered across severity sections -- see GitHub issue #10's first comment. Components are
sorted by their worst severity, so a FAIL-bearing component still sorts before a WARN-only one;
by default a component is shown only if at least one of its findings is not PASSED, and then
*all* of its findings (including the PASSED ones) render together -- that adjacency is the point
of the change. ``show_passed`` additionally reveals components whose findings are all PASSED.

Within a shown component, findings render **structure before metadata**, then by severity, then
by check and type -- so the two validators' takes on the same component never interleave
arbitrarily (see :meth:`Report._render_components`). Every component (and the global-attribute
block below) prints a **header row** naming its columns, built from the same column spec
(:class:`_Column`) the finding rows themselves are built from, so the header can never drift out
of sync with what it labels.

**Global attributes get their own compact block**, above a produced file's components: a global
attribute only ever produces one kind of line (an existence check), so ``source``/``scope``/
``check`` are constant across the whole block and are dropped in favour of just the attribute
name, what was found, and its severity.

:meth:`Report.write_csv` (P1-9.4) exports every raw finding -- one row per occurrence, no
deduplication -- as the triage escape hatch for the full per-file detail.

A finding seen in exactly one file names that file's basename inline in place of the count
(``x1 file``), always. A finding seen in more than one file still renders just the count by
default; passing ``show_files`` also lists each distinct basename beneath the finding line, one
per line, capped at ``max_files`` with a trailing ``... and N more`` line when truncated -- the
mount-scale case where dozens of files share a finding still fits on screen.

A module/filepath heading is only rendered when it has at least one component (or global
attribute) beneath it, so a module whose components all passed (and are hidden by default) does
not print a heading with nothing under it.
"""

import csv
import importlib.metadata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from functools import cached_property
from pathlib import Path

from cit.contract import Contract

_DISTRIBUTION_NAME = "confluence-contracts"

# Public (no leading underscore): imported across module boundaries by orchestrate.py and
# __main__.py so the default lives in exactly one place.
DEFAULT_MAX_FILES = 5

# Below this many distinct files, _files_suffix already names the lone file inline, so
# _files_lines has nothing left to add.
_MIN_FILES_TO_LIST = 2

_INDENT = " " * 4


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
        MISSING: The contract or rules declare a component the result file does not contain.
        EXTRA: The result file contains a component the contract or rules do not declare (drift).
        DIFFERENT: The component exists on both sides but its structure or metadata disagrees.
        PASSED: The component exists on both sides and every check agreed.
        SKIPPED: CIT could not run this check at all -- a gap in the tool, not in the data. Kept
            last in the enum since it sits outside the found/not-found ladder the other four
            members form.
    """

    MISSING = "MISSING"
    EXTRA = "EXTRA"
    DIFFERENT = "DIFFERENT"
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"


class FindingStatus(StrEnum):
    """How a finding bears on the run's exit policy.

    Attributes:
        FAIL: A broken interface guarantee; fails the run.
        WARN: Drift or an absent optional component; reported without failing.
        INFO: A check that agreed; carried so a report can show what was verified.
        REPORT: A report-only observation (e.g. a P1-16 health check, or a check CIT cannot run
            at all) that is always shown but never affects the exit code, even under
            ``--strict``.
    """

    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"
    REPORT = "REPORT"


class ValidationSource(StrEnum):
    """Which validator produced a finding -- the closed vocabulary for ``Finding.validation``.

    Defined once here (rather than as string literals scattered across the two ``_Reporter``
    construction sites in :mod:`cit.validation`) so the CSV header, the report text and any
    future validator all agree on the same two spellings.

    Attributes:
        STRUCTURE: Compared against the module's contract (``contracts/<module>.yml``).
        METADATA: Compared against the SoS rules (``rules/sos_results_rules.yml``).
    """

    STRUCTURE = "structure"
    METADATA = "metadata"


class Check(StrEnum):
    """The specific question a finding's check asked -- a closed, eight-label vocabulary.

    A different axis from :class:`ValidationSource` (which validator asked) and from a finding's
    ``scope`` (what kind of thing was examined): ``check`` names *which* question about that
    thing was asked, so e.g. a variable's dtype check and its dims check -- two different
    questions about the same variable -- render on distinguishable lines. Defined once here,
    rather than as string literals scattered across the 14 sites in :mod:`cit.validation` that
    emit one, and reused by :data:`_CHECKS_BY_SOURCE` so the legend cannot fall out of sync with
    the vocabulary.

    Attributes:
        EXISTS: The dimension, variable or global attribute is declared/required and present, or
            vice versa.
        DTYPE: The variable's data type matches the one declared.
        DIMS: The variable's dimension names, and their order, match.
        DTYPE_DIMS: Both the dtype and dims checks agreed -- reported once instead of two PASSED
            lines.
        ATTRS: The variable carries the attribute names the SoS spec declares for it.
        REQUIRED: ``long_name``, ``units`` and ``coverage_content_type`` are present and
            non-blank.
        BOUNDS: ``valid_min`` is not greater than ``valid_max``.
        FILL: The declared fill value is the canonical one for the variable's data type.
    """

    EXISTS = "exists"
    DTYPE = "dtype"
    DIMS = "dims"
    DTYPE_DIMS = "dtype+dims"
    ATTRS = "attrs"
    REQUIRED = "required"
    BOUNDS = "bounds"
    FILL = "fill"


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
        validation: Which validation produced the finding (``structure`` or ``metadata``, see
            :class:`ValidationSource`), so a report can group by source and ``--strict`` can
            escalate only metadata-rule findings.
        message: Optional detail, e.g. the disagreeing contract and result values.
        results_file: The resolved produced file the finding came from (e.g.
            ``flpe/momma/74267700071_momma.nc``), distinct from ``filepath``'s path template.
        scope: What kind of thing was examined -- ``dimension``, ``variable``, ``attribute`` or
            ``global_attribute`` -- a different axis from ``validation`` (structure vs metadata)
            and from ``check`` (which question was asked about it). Has no default: every
            finding must name what it examined, so a missing value fails loudly at construction
            rather than silently grouping unlike findings together.
        check: The specific question the check asked (see :class:`Check`), e.g. ``dtype`` vs
            ``dims`` for two different questions about the same variable. Also required, for the
            same reason ``scope`` is.
    """

    type: FindingType
    status: FindingStatus
    module_name: str
    component: str
    filepath: str
    validation: str
    message: str = ""
    results_file: str = ""
    scope: str = field(kw_only=True)
    check: Check = field(kw_only=True)


def _sort_key(finding: Finding) -> tuple[int, str, str, str, str, str]:
    """Deterministic ordering key: severity first, then a stable tiebreaker.

    Used both to order the findings within one group and to order deduplicated entries, so
    rendering never depends on dict/set iteration order.

    Args:
        finding: The finding to compute an ordering key for.

    Returns:
        A tuple ordering by severity (:data:`_SEVERITY`), then module, component, scope, message
        and finally the contract's filepath template.
    """
    return (
        _SEVERITY[finding.status],
        finding.module_name,
        finding.component,
        finding.scope,
        finding.message,
        finding.filepath,
    )


# Column widths: each the longer of its header word and its widest possible value, so a header
# row and its finding rows always agree on alignment. Kept as module-level constants (rather than
# computed inline in _GRID) so the docstring above and a reader scanning the module can see the
# grid's shape at a glance.
_SOURCE_WIDTH = len("structure")
_SCOPE_WIDTH = len("global_attribute")
_CHECK_WIDTH = len(Check.DTYPE_DIMS)
_FOUND_WIDTH = len(FindingType.DIFFERENT)
_SEVERITY_WIDTH = len("severity")  # the header word "severity" outruns every status value


@dataclass(frozen=True)
class _Column:
    """One column in a finding grid: its header text, width, and how to read a finding's value.

    The header row and every finding row are built from the same sequence of ``_Column``s (see
    :func:`_grid_header` and :func:`_grid_row`), so there is exactly one place that declares what
    the columns are, how wide they are, and what order they render in -- a header row and a row
    beneath it can never drift out of sync by hand-editing one and not the other.

    Attributes:
        header: The column's header-row text (e.g. ``"severity"``).
        width: How many characters to left-justify the column to.
        value: Maps a finding to the text this column renders for it.
    """

    header: str
    width: int
    value: Callable[[Finding], str]


# The six-column grid every component renders: source, scope, check, found, severity, then a
# trailing, unwidened files column appended by _grid_header/_grid_row. The global-attribute block
# renders its own three-column variant (see Report._render_global_attributes) since source, scope
# and check are constant there and dropped.
_GRID: tuple[_Column, ...] = (
    _Column("source", _SOURCE_WIDTH, lambda finding: finding.validation),
    _Column("scope", _SCOPE_WIDTH, lambda finding: finding.scope),
    _Column("check", _CHECK_WIDTH, lambda finding: finding.check),
    _Column("found", _FOUND_WIDTH, lambda finding: finding.type),
    _Column("severity", _SEVERITY_WIDTH, lambda finding: finding.status),
)


# Checks grouped by the validator that runs them, in render order, feeding the legend's "Checks"
# block (see _checks_block). A tuple of (Check, description) pairs rather than a dict, since
# "exists" means something different under each source and a dict would collapse the two.
_CHECKS_BY_SOURCE: tuple[tuple[ValidationSource, str, tuple[tuple[Check, str], ...]], ...] = (
    (
        ValidationSource.STRUCTURE,
        "compared against the module's contract (contracts/<module>.yml)",
        (
            (Check.EXISTS, "the dimension or variable is declared and present in the file"),
            (Check.DTYPE, "the variable's data type matches the one declared"),
            (Check.DIMS, "the variable's dimension names, and their order, match"),
            (Check.DTYPE_DIMS, "both agreed -- reported once instead of two PASSED lines"),
        ),
    ),
    (
        ValidationSource.METADATA,
        "compared against the SoS rules (rules/sos_results_rules.yml)",
        (
            (Check.EXISTS, "the spec covers this variable, or requires this global attribute"),
            (Check.ATTRS, "the variable carries the attribute names the spec declares for it"),
            (
                Check.REQUIRED,
                "long_name, units and coverage_content_type are present and non-blank",
            ),
            (Check.BOUNDS, "valid_min is not greater than valid_max"),
            (Check.FILL, "the fill value is the canonical one for the variable's data type"),
        ),
    ),
)


def _checks_block() -> str:
    """Build the legend's "Checks" block from :data:`_CHECKS_BY_SOURCE`.

    Rendering this from the same data that names each check (rather than a hand-written block of
    text) means a ninth check cannot be added to :class:`Check` and silently leave the legend
    stale: the assertion below fails loudly the first time the new check is exercised, exactly
    the same "fail loudly rather than silently" contract ``Finding.check`` itself relies on.

    Returns:
        The "Checks" heading, then one ``<source>  <what it compares against>`` line per
        validator followed by one indented ``<check>  <description>`` line per check it runs.
    """
    covered = {check for _, _, checks in _CHECKS_BY_SOURCE for check, _ in checks}
    assert covered == set(Check), "every Check must be documented in the legend"

    label_width = max(len(check) for _, _, checks in _CHECKS_BY_SOURCE for check, _ in checks)
    source_width = max(len(source) for source, _, _ in _CHECKS_BY_SOURCE) + 3
    lines = ["Checks -- the question each line asked.", ""]
    for source, compared_against, checks in _CHECKS_BY_SOURCE:
        lines.append(f"{source:<{source_width}}{compared_against}")
        lines.extend(f"  {check:<{label_width}}  {description}" for check, description in checks)
        lines.append("")
    return "\n".join(lines).rstrip()


_LEGEND = f"""\
Declared = what the contract (structure) or the SoS rules (metadata) say should be there.
Found    = what the produced file actually holds.

PASSED     Declared and found, contract/rules match module file.
MISSING    Declared, but not found in the file. Data is missing from the module file.
EXTRA      Found in the file, but not declared. Extra data located in the module file.
DIFFERENT  Declared and found, contract/rules do not match module file. Message indicates
           values for both.
SKIPPED    CIT could not run this check -- a gap in the tool, not in the data.

{_checks_block()}"""


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
        show_files: bool = False,
        max_files: int = DEFAULT_MAX_FILES,
    ) -> None:
        """Store the findings to aggregate, plus what ``__str__`` needs to render them.

        ``contracts``, ``show_passed``, ``show_files``, and ``max_files`` are all optional so
        existing ``Report(findings)`` callers (including every test written before P1-9.3) keep
        working unchanged; without them ``__str__`` still renders, just with a degraded banner,
        PASSED-only components hidden, and no per-file lists beneath multi-file findings.

        Args:
            findings: The check outcomes collected across a validation run.
            contracts: The contracts loaded for this run, keyed by module name (e.g.
                ``Orchestrate.contracts``), used to print each module's version/branch/commit in
                the banner. ``None`` renders a banner with no per-module version data.
            show_passed: When True, ``__str__`` also renders components whose findings are all
                PASSED, not just the ones with at least one non-PASSED finding.
            show_files: When True, ``__str__`` also lists the distinct result-file basenames
                beneath a finding seen in more than one file (a lone file is always named
                inline, regardless of this flag).
            max_files: How many basenames to list per finding when ``show_files`` is set, before
                truncating with a ``... and N more`` line.
        """
        self._findings = list(findings)
        self._contracts = contracts or {}
        self._show_passed = show_passed
        self._show_files = show_files
        self._max_files = max_files

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

        Report does not re-escalate anything itself: ``--strict`` promotion of metadata-rule
        WARNs to FAILs already happens at emit time in ``validation._status``, which keeps a
        finding's status truthful at the point it was produced. Report only consumes the status
        it is given. Measured asymmetry from a real mount run: ``--strict`` moved 171 rule
        findings WARN -> FAIL and left 92 untouched, because ``partition`` hardcodes ``EXTRA`` to
        WARN -- an extra attribute is drift, not a violation, and staying WARN under ``--strict``
        reflects that.
        """
        return 1 if any(finding.status is FindingStatus.FAIL for finding in self._findings) else 0

    @cached_property
    def deduplicated(self) -> tuple[DedupedFinding, ...]:
        """Collapse findings that differ only by ``results_file`` into one entry each.

        At mount scale the same findings recur once per produced file (e.g. the same 39 momma
        findings printed 19 times, once per reach file); deduplicating on every field except
        ``results_file`` turns that repetition into one entry annotated with how many files --
        and which ones -- it was seen in. Computed once per :class:`Report` instance and cached:
        ``self._findings`` never changes after construction (``__init__`` copies its argument),
        so this only ever needs to run once. Returns a tuple of frozen ``DedupedFinding``
        entries (whose own ``files`` field is already a tuple) rather than a list, so the whole
        result is immutable and a caller cannot corrupt the cache for a later read -- unlike a
        plain method, a ``cached_property`` cannot hand back a defensive copy per access, since
        the cached attribute *is* the returned object.

        Returns:
            One :class:`DedupedFinding` per distinct finding (all fields but ``results_file``),
            sorted deterministically by :func:`_sort_key`.
        """
        entries: dict[Finding, list[str]] = defaultdict(list)
        for finding in self._findings:
            entries[replace(finding, results_file="")].append(finding.results_file)
        deduped = [
            DedupedFinding(finding=key, count=len(files), files=tuple(sorted(set(files))))
            for key, files in entries.items()
        ]
        return tuple(sorted(deduped, key=lambda entry: _sort_key(entry.finding)))

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

        Unlike ``__str__`` (deduplicated for readability) or :attr:`deduplicated`, this is the
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
            ``show_passed``/``show_files``/``max_files`` -- byte-identical across runs on the
            same inputs.
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
        if body:
            sections.append(body)
        elif not self.deduplicated:
            sections.append("(no findings)")
        else:
            sections.append(
                "(no findings to show -- every component passed; use --show-passed to list them)"
            )
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
        deduped = self.deduplicated
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
        raw occurrences it collapsed (see :attr:`deduplicated`). Within each produced-file group,
        global-attribute findings are pulled out into their own compact block (see
        :meth:`_render_global_attributes`) before the remaining findings render as components
        exactly as before.

        Returns:
            The rendered findings, or ``""`` if there is nothing to show.
        """
        deduped = self.deduplicated
        if not deduped:
            return ""

        by_finding = {entry.finding: entry for entry in deduped}
        representatives = [entry.finding for entry in deduped]

        # Reuse grouped_by by wrapping each axis's slice in a throwaway Report
        module_groups = Report(representatives).grouped_by(lambda finding: finding.module_name)
        lines: list[str] = []
        for module_name, module_findings in module_groups.items():
            module_lines: list[str] = []
            file_groups = Report(module_findings).grouped_by(lambda finding: finding.filepath)
            for filepath, file_findings in file_groups.items():
                global_attributes = [f for f in file_findings if f.scope == "global_attribute"]
                components = [f for f in file_findings if f.scope != "global_attribute"]
                file_lines = [
                    *self._render_global_attributes(global_attributes, by_finding),
                    *self._render_components(components, by_finding),
                ]
                if not file_lines:
                    continue
                module_lines.append(f"{_INDENT}{filepath}")
                module_lines.extend(file_lines)
            if not module_lines:
                continue
            lines.append(module_name)
            lines.extend(module_lines)
        return "\n".join(lines)

    def _render_grid_block(
        self,
        heading: str,
        columns: tuple[_Column, ...],
        findings: list[Finding],
        by_finding: dict[Finding, DedupedFinding],
    ) -> list[str]:
        """Render one heading, its header row, then one (or more) lines per finding.

        The shared "heading, header row, rows" shape behind both a component (called once per
        component, with that component's own findings) and the global-attribute block (called
        once per produced file, with every surviving attribute finding flattened into it) --
        the two differ only in the heading text, which findings are shown, and the column spec.

        Args:
            heading: The already-indented heading line (a component name, or "global
                attributes").
            columns: The column spec (see :class:`_Column`), shared by the header row and every
                finding row so they cannot drift out of sync.
            findings: The findings to render beneath the heading, already in display order.
            by_finding: Maps a representative finding back to its :class:`DedupedFinding`, for
                the occurrence count and file list a plain ``Finding`` no longer carries.

        Returns:
            The heading, the header row, then one line per finding, plus a message line and/or
            ``--show-files`` lines where applicable.
        """
        continuation = _grid_continuation(columns)
        lines = [heading, f"{_INDENT * 3}{_grid_header(columns)}"]
        for finding in findings:
            entry = by_finding[finding]
            lines.append(f"{_INDENT * 3}{_grid_row(finding, columns, _files_suffix(entry))}")
            if finding.message:
                lines.append(f"{_INDENT * 3}{continuation}{finding.message}")
            if self._show_files:
                lines.extend(_files_lines(entry, self._max_files, continuation))
        return lines

    def _render_components(
        self, findings: list[Finding], by_finding: dict[Finding, DedupedFinding]
    ) -> list[str]:
        """Render one produced file's components, ordered by worst severity then name.

        Args:
            findings: The deduplicated, representative findings for one module/filepath group,
                with any global-attribute findings already pulled out by the caller.
            by_finding: Maps a representative finding back to its :class:`DedupedFinding` (for
                the occurrence count and file list a plain ``Finding`` no longer carries).

        Returns:
            One heading, one header row, and one (or two, if there is a message) lines per
            finding, for every component that is shown -- skipping PASSED-only components unless
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
            # grouped_by already sorted by severity; re-sort so structure precedes metadata
            # within a component, tiebroken by check and type, without changing _sort_key
            # itself (which also orders the deduplicated entries and the CSV rows).
            ordered_findings = sorted(
                component_findings,
                key=lambda finding: (
                    finding.validation != ValidationSource.STRUCTURE,
                    _SEVERITY[finding.status],
                    finding.check,
                    finding.type,
                ),
            )
            lines.extend(
                self._render_grid_block(
                    f"{_INDENT * 2}{component}", _GRID, ordered_findings, by_finding
                )
            )
        return lines

    def _render_global_attributes(
        self, findings: list[Finding], by_finding: dict[Finding, DedupedFinding]
    ) -> list[str]:
        """Render one produced file's global attributes as a single compact block.

        A global attribute only ever produces one kind of line (an existence check against the
        SoS rules), so ``source``, ``scope`` and ``check`` are constant across the whole block --
        this drops them in favour of just the attribute name, what was found, and its severity,
        rather than rendering the full six-column grid (and a header row) once per attribute.

        Args:
            findings: The global-attribute findings for one produced file (``scope ==
                "global_attribute"``); already isolated by the caller.
            by_finding: Maps a representative finding back to its :class:`DedupedFinding`.

        Returns:
            The block's heading, header row, and one line per shown attribute -- or ``[]`` when
            there are no global-attribute findings at all, or every one of them passed and
            ``show_passed`` is not set, matching the dangling-heading rule components already
            follow.
        """
        if not findings:
            return []

        groups = Report(findings).grouped_by(lambda finding: finding.component)
        shown = [
            finding
            for component_findings in groups.values()
            if self._show_passed
            or not all(f.type == FindingType.PASSED for f in component_findings)
            for finding in component_findings
        ]
        if not shown:
            return []

        attribute_width = max(len("attribute"), *(len(finding.component) for finding in shown))
        columns = (
            _Column("attribute", attribute_width, lambda finding: finding.component),
            _Column("found", _FOUND_WIDTH, lambda finding: finding.type),
            _Column("severity", _SEVERITY_WIDTH, lambda finding: finding.status),
        )
        return self._render_grid_block(
            f"{_INDENT * 2}global attributes", columns, shown, by_finding
        )


def _grid_header(columns: tuple[_Column, ...]) -> str:
    """Build a grid's header row from its column spec.

    Args:
        columns: The column spec to render (see :class:`_Column`).

    Returns:
        Each column's header text, left-justified to its width and separated by one space, plus
        a trailing ``files`` for the always-unwidened file-count column every grid ends with.
    """
    return " ".join(f"{column.header:<{column.width}}" for column in columns) + " files"


def _grid_row(finding: Finding, columns: tuple[_Column, ...], files_suffix: str) -> str:
    """Build one finding's row from the same column spec its header row used.

    Args:
        finding: The finding to render.
        columns: The column spec (see :class:`_Column`), identical to the one passed to the
            header row this line renders beneath.
        files_suffix: How many/which files the finding was seen in (see :func:`_files_suffix`).

    Returns:
        Each column's value, left-justified to its width and separated by one space, plus the
        files suffix -- trailing whitespace stripped when there is no suffix to show.
    """
    prefix = " ".join(f"{column.value(finding):<{column.width}}" for column in columns)
    return f"{prefix} {files_suffix}".rstrip()


def _grid_continuation(columns: tuple[_Column, ...]) -> str:
    """Compute a grid's continuation indent from the same column spec it was rendered with.

    A message line and a --show-files basename both continue beneath the files column, so they
    must be indented to wherever that grid's files column actually starts -- which depends on
    the column spec being rendered, not on the six-column component grid specifically. Deriving
    it from ``columns`` here (rather than reading a single module-level constant sized for one
    grid) is what lets the global-attribute block's narrower, three-column spec get its own,
    narrower continuation instead of inheriting the component grid's.

    Args:
        columns: The column spec the grid was rendered from (see :func:`_grid_header`).

    Returns:
        One space per column width plus one separator per column -- the same arithmetic
        :func:`_grid_header`/:func:`_grid_row` produce by construction, so a continuation line
        lands exactly where that grid's ``files`` column starts.
    """
    return " " * (sum(column.width for column in columns) + len(columns))


def _files_suffix(entry: DedupedFinding) -> str:
    """Describe how many distinct results files a deduplicated finding was seen in.

    Tolerates a finding with no file at all (e.g. P1-17's registry cross-check, which has
    nothing on disk behind it): if every ``entry.files`` value is empty, no suffix is rendered.
    A finding seen in exactly one file names that file's basename instead of the otherwise
    useless ``x1 file`` -- the count is pure noise when there is only one file to begin with.

    Args:
        entry: The deduplicated finding to describe.

    Returns:
        The lone file's basename if there is exactly one, ``"x<n> files"`` for more than one, or
        ``""`` when there is no file behind the finding at all.
    """
    real_files = [f for f in entry.files if f]
    if not real_files:
        return ""
    if len(real_files) == 1:
        return Path(real_files[0]).name
    return f"x{len(real_files)} files"


def _files_lines(entry: DedupedFinding, max_files: int, continuation: str) -> list[str]:
    """Render the indented basenames to list beneath a multi-file finding, capped and sorted.

    A single file is never listed here even when present -- :func:`_files_suffix` already names
    it inline, and listing it again would be a redundant one-line list under every finding.

    Args:
        entry: The deduplicated finding whose files to list.
        max_files: How many basenames to render before truncating.
        continuation: The indent to render each basename at, matching the grid's files column
            (see :func:`_grid_continuation`) -- the caller's column spec, not a fixed constant,
            decides this.

    Returns:
        One already-indented line per basename (up to ``max_files``, in ``entry.files``' sorted
        order), plus a trailing ``... and N more (use --csv for the full list)`` line when
        truncated. Empty when there are fewer than two non-empty files.
    """
    real_files = [f for f in entry.files if f]
    if len(real_files) < _MIN_FILES_TO_LIST:
        return []

    shown = real_files[:max_files]
    lines = [f"{_INDENT * 3}{continuation}{Path(f).name}" for f in shown]
    remaining = len(real_files) - len(shown)
    if remaining:
        lines.append(
            f"{_INDENT * 3}{continuation}... and {remaining} more (use --csv for the full list)"
        )
    return lines
