"""Findings and reporting: the shared output vocabulary for every check.

Defines the ``Finding`` every validator emits and the ``Report`` that aggregates, dedupes,
groups and renders them. A finding models five axes separately -- ``validation``, ``scope``,
``check``, ``type`` and ``status`` -- rather than collapsing them into one overloaded ``check``
string, so two checks that ask different questions can never render byte-identical.

``Report.__str__`` splits the findings body into one top-level section per
:class:`ValidationSource` (structure, then metadata), so a reader chasing contract breakage does
not wade through metadata drift and vice versa; an empty section, or one ``checks`` excludes,
renders nothing at all, heading included. Within a section, findings group **component-first**
(module, then produced-file template, then component) rather than severity-first, so one
variable's agreeing and disagreeing checks land together instead of scattered across severity
sections. Components sort by worst severity; ``show_passed`` is a **per-line** rule, so a PASSED
finding renders only when it is set, and an attribute-scoped finding nests beneath its parent
variable rather than rendering as a component of its own. Global attributes get their own compact
block above a file's components, since only one (metadata-only) kind of check ever applies to
them, so that block now only ever appears in the metadata section. :meth:`Report.write_csv`
exports every raw finding, undeduplicated, as the full per-file detail a text report collapses
away.
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

_LEGEND = """\
Declared = what the contract (structure) or the SoS rules (metadata) say should be there.
Found    = what the produced file actually holds.

PASSED     Declared and found, contract/rules match module file.
MISSING    Declared, but not found in the file. Data is missing from the module file.
EXTRA      Found in the file, but not declared. Extra data located in the module file.
DIFFERENT  Declared and found, contract/rules do not match module file. Message indicates
           values for both.
SKIPPED    CIT could not run this check -- a gap in the tool, not in the data."""


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

    Kept as an enum rather than scattered string literals, so the CSV header, the report text
    and any future validator all agree on the same two spellings.

    Attributes:
        STRUCTURE: Compared against the module's contract (``contracts/<module>.yml``).
        METADATA: Compared against the SoS rules (``rules/sos_results_rules.yml``).
    """

    STRUCTURE = "structure"
    METADATA = "metadata"


class Check(StrEnum):
    """The specific question a finding's check asked -- a closed, eight-label vocabulary.

    A different axis from :class:`ValidationSource` (which validator asked) and from a finding's
    ``scope`` (what kind of thing was examined): ``check`` names *which* question was asked, so
    e.g. a variable's dtype check and its dims check render on distinguishable lines.

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
    # REPORT sorts last: it never affects the exit code, and a report-only note must never
    # outrank a real pass/fail/warn finding when a group is ranked by worst severity.
    FindingStatus.REPORT: 3,
}


# Column widths: each the longer of its header word and its widest possible value. Two read an
# enum member, so the block sits here rather than with the dependency-free constants above.
_SOURCE_WIDTH = len("structure")
# "global_attribute" never reaches this grid -- it renders only in its own block (see
# _render_global_attributes) -- so the widest scope value here is "dimension"/"attribute".
_SCOPE_WIDTH = len("dimension")
_CHECK_WIDTH = len(Check.DTYPE_DIMS)
_FOUND_WIDTH = len(FindingType.DIFFERENT)
_SEVERITY_WIDTH = len("severity")  # the header word "severity" outruns every status value


# Checks grouped by validator, in render order, feeding the legend (see _checks_block). A tuple
# of (Check, description) pairs, not a dict -- "exists" means different things per source.
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
        validation: Which validation produced the finding (see :class:`ValidationSource`).
        message: Optional detail, e.g. the disagreeing contract and result values.
        results_file: The resolved produced file the finding came from, distinct from
            ``filepath``'s path template.
        scope: What kind of thing was examined (``dimension``, ``variable``, ``attribute`` or
            ``global_attribute``); required, so a missing value fails loudly at construction
            rather than grouping silently.
        check: The specific question asked (see :class:`Check`), e.g. ``dtype`` vs ``dims`` for
            two questions about the same variable; required for the same reason as ``scope``.
        parent: The variable name an attribute-scoped finding belongs to, or ``""`` otherwise;
            explicit rather than inferred by splitting ``component`` on ``.``, since a
            variable's own name may itself contain a dot (``Qmean_momma.constrained``).
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
    parent: str = field(default="", kw_only=True)


@dataclass(frozen=True)
class _Column:
    """One column in a finding grid: its header text, width, and how to read a finding's value.

    Header and finding rows are built from the same sequence of ``_Column``s (see
    :func:`_grid_header` and :func:`_grid_row`), so they cannot drift out of sync by
    hand-editing one and not the other.

    Attributes:
        header: The column's header-row text (e.g. ``"severity"``).
        width: How many characters to left-justify the column to.
        value: Maps a finding to the text this column renders for it.
    """

    header: str
    width: int
    value: Callable[[Finding], str]


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


# The six-column grid every component renders. The global-attribute block (see
# Report._render_global_attributes) renders its own narrower three-column variant.
_GRID: tuple[_Column, ...] = (
    _Column("source", _SOURCE_WIDTH, lambda finding: finding.validation),
    _Column("scope", _SCOPE_WIDTH, lambda finding: finding.scope),
    _Column("check", _CHECK_WIDTH, lambda finding: finding.check),
    _Column("found", _FOUND_WIDTH, lambda finding: finding.type),
    _Column("severity", _SEVERITY_WIDTH, lambda finding: finding.status),
)

# _GRID minus its "source" column -- a section names its source in the heading already, so a
# component row need not repeat it; derived, not hand-written, so the two specs stay in sync.
_SECTION_GRID: tuple[_Column, ...] = tuple(column for column in _GRID if column.header != "source")

# What each section heading says the source was compared against -- keyed by ValidationSource so
# a third source cannot be named without also being added here.
_SECTION_COMPARISON: dict[ValidationSource, str] = {
    ValidationSource.STRUCTURE: "the module contract",
    ValidationSource.METADATA: "the SoS rules",
}


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
        checks: ValidationSource | None = None,
    ) -> None:
        """Store the findings to aggregate, plus what ``__str__`` needs to render them.

        All keyword arguments are optional, so an existing ``Report(findings)`` caller keeps
        working unchanged -- just with a degraded banner and every PASSED finding hidden.

        Args:
            findings: The check outcomes collected across a validation run.
            contracts: This run's contracts, keyed by module name, used to print each module's
                version/branch/commit in the banner. ``None`` renders no version data.
            show_passed: Also render PASSED findings, not just a component's non-PASSED ones.
            show_files: Also list the distinct result-file basenames beneath a finding seen in
                more than one file (a lone file is always named inline regardless of this flag).
            max_files: How many basenames to list per finding when ``show_files`` is set, before
                truncating with a ``... and N more`` line.
            checks: Render only this source's section, not both. Rendering-only: never changes
                :attr:`exit_code`, the counts line, or :meth:`write_csv`.
        """
        self._findings = list(findings)
        self._contracts = contracts or {}
        self._show_passed = show_passed
        self._show_files = show_files
        self._max_files = max_files
        self._checks = checks

    @property
    def findings(self) -> list[Finding]:
        """The raw, undeduplicated findings collected across the run.

        Kept accessible (not just internal) so a full export -- e.g. the CSV, which needs every
        occurrence rather than one deduplicated entry -- can still get at each one.

        Returns:
            A new list of every :class:`Finding` passed to the constructor.
        """
        return list(self._findings)

    @property
    def exit_code(self) -> int:
        """The run's exit code: 1 if any finding is FAIL, else 0.

        REPORT findings never affect this, even under ``--strict``. Report does not re-escalate
        anything itself -- ``--strict`` promotion of metadata-rule WARNs to FAILs already
        happens at emit time in ``validation._status``, so a finding's status stays truthful at
        the point it was produced; Report only consumes what it is given.

        Returns:
            1 if any finding's status is FAIL, else 0.
        """
        return 1 if any(finding.status is FindingStatus.FAIL for finding in self._findings) else 0

    @cached_property
    def deduplicated(self) -> tuple[DedupedFinding, ...]:
        """Collapse findings that differ only by ``results_file`` into one entry each.

        At mount scale the same findings recur once per produced file (e.g. 39 momma findings
        printed 19 times, once per reach file); deduplicating on every field but
        ``results_file`` turns that into one entry annotated with which files it was seen in.
        Cached since ``self._findings`` never changes after construction, and returned as an
        immutable tuple of frozen entries so a caller cannot corrupt a ``cached_property``'s
        cache by mutating what it hands back.

        Returns:
            One :class:`DedupedFinding` per distinct finding, sorted by :func:`_sort_key`.
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

        One generic helper rather than a method per axis, since rendering already groups by
        module, file template, component, and attribute parent.

        Args:
            key: Maps a finding to the string to group it by (e.g. its module name).

        Returns:
            A dict from group key to its findings, both key order and each group's order sorted
            so rendering never depends on dict/set iteration order.
        """
        groups: dict[str, list[Finding]] = {}
        for finding in self._findings:
            groups.setdefault(key(finding), []).append(finding)
        return {
            group_key: sorted(groups[group_key], key=_sort_key) for group_key in sorted(groups)
        }

    def write_csv(self, path: str | Path) -> None:
        """Write every raw finding to ``path`` as CSV, one row per occurrence.

        Unlike ``__str__`` or :attr:`deduplicated`, this is the un-deduplicated escape hatch for
        triage: every ``results_file`` a finding was seen in gets its own row. Uses only the
        stdlib ``csv`` module -- pandas/rich/tabulate are not runtime dependencies of CIT.

        Args:
            path: Where to write the CSV file; overwritten if it already exists.
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
        """Render the banner, legend, checks block, counts line, and section-grouped findings.

        The locked API is ``print(report)``, so rendering lives on ``__str__`` rather than a
        separate ``render()`` method; anything that varies rendering is therefore a constructor
        argument rather than a call-time one.

        Returns:
            The full report text, ready to print.
        """
        sections = [
            self._banner(),
            "",
            _LEGEND,
            "",
            _checks_block(),
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
        """Render one section per :class:`ValidationSource` present, structure then metadata.

        Partitions the deduplicated findings by source and calls :meth:`_render_section` once
        per source that survives the run's ``checks`` filter -- the same module -> file ->
        component walk renders both sections rather than a second traversal. A source with no
        surviving findings (or excluded by ``checks``) contributes no section at all, heading
        included.

        Returns:
            The rendered sections joined by a blank line, or ``""`` if there is nothing to show.
        """
        deduped = self.deduplicated
        if not deduped:
            return ""

        by_finding = {entry.finding: entry for entry in deduped}
        sections: list[str] = []
        for source in ValidationSource:
            if self._checks is not None and source != self._checks:
                continue
            representatives = [
                entry.finding for entry in deduped if entry.finding.validation == source
            ]
            body = self._render_section(representatives, by_finding, depth=1)
            if body:
                sections.append(f"{_section_heading(source)}\n{body}")
        return "\n\n".join(sections)

    def _render_section(
        self,
        representatives: list[Finding],
        by_finding: dict[Finding, DedupedFinding],
        depth: int,
    ) -> str:
        """Render one source's findings, grouped module -> produced-file template -> component.

        ``depth`` is the module heading's indent level; every level below it (file, component,
        grid) is derived from it, so a caller shifts the whole walk by passing one number rather
        than by re-indenting each level by hand. Global-attribute findings are pulled into their
        own compact block (see :meth:`_render_global_attributes`) ahead of the rest of each
        file's components.

        Args:
            representatives: One deduplicated finding per distinct occurrence, all from this
                source.
            by_finding: Maps each representative back to its full :class:`DedupedFinding` (count
                and files).
            depth: The module heading's indent level.

        Returns:
            The rendered findings, or ``""`` if there is nothing to show.
        """
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
                    *self._render_global_attributes(global_attributes, by_finding, depth + 2),
                    *self._render_components(components, by_finding, depth + 2),
                ]
                if not file_lines:
                    continue
                module_lines.append(f"{_INDENT * (depth + 1)}{filepath}")
                module_lines.extend(file_lines)
            if not module_lines:
                continue
            lines.append(f"{_INDENT * depth}{module_name}")
            lines.extend(module_lines)
        return "\n".join(lines)

    def _render_grid_block(
        self,
        heading: str,
        columns: tuple[_Column, ...],
        findings: list[Finding],
        by_finding: dict[Finding, DedupedFinding],
        *,
        header: bool = True,
        indent: int,
    ) -> list[str]:
        """Render one heading, optionally its header row, then one (or more) lines per finding.

        Shared by a component's own findings, a nested attribute sub-block (``header=False``,
        since one header already covers the whole component), and the global-attribute block --
        they differ only in heading text, findings shown, column spec, and the caller-computed
        ``indent``.

        Args:
            heading: The already-indented heading line to render first.
            columns: The column spec both the header row and each finding row are built from.
            findings: The findings to render, one grid row each, in the order given.
            by_finding: Maps each finding back to its full :class:`DedupedFinding` (count and
                files), for the files suffix and ``--show-files`` lines.
            header: Whether to render the header row; ``False`` when a preceding block's header
                already covers this one.
            indent: How many indent levels to render the header and each finding row at.

        Returns:
            The heading, the header row (if ``header``), then one line per finding, plus a
            message line and/or ``--show-files`` lines where applicable.
        """
        continuation = _grid_continuation(columns)
        message_offset = _grid_message_offset(columns)
        lines = [heading]
        if header:
            lines.append(f"{_INDENT * indent}{_grid_header(columns)}")
        for finding in findings:
            entry = by_finding[finding]
            lines.append(f"{_INDENT * indent}{_grid_row(finding, columns, _files_suffix(entry))}")
            if finding.message:
                lines.append(f"{_INDENT * indent}{message_offset}{finding.message}")
            if self._show_files:
                lines.extend(_files_lines(entry, self._max_files, continuation, indent))
        return lines

    def _render_components(
        self, findings: list[Finding], by_finding: dict[Finding, DedupedFinding], depth: int
    ) -> list[str]:
        """Render one produced file's components, ordered by worst severity then name.

        ``depth`` is the component heading's indent level, passed through to
        :meth:`_render_component_block`. ``show_passed`` is a per-line rule: a PASSED finding is
        dropped unless it is set, and a component (own findings plus any nested attribute
        findings) is skipped only once nothing survives that filter -- superseding the old
        all-PASSED-component rule rather than adding to it.

        Args:
            findings: This file's non-global-attribute findings, both component-level and
                attribute-scoped.
            by_finding: Maps each finding back to its full :class:`DedupedFinding` (count and
                files).
            depth: The component heading's indent level.

        Returns:
            One heading, one header row, and one (or two, if there is a message) lines per
            finding, for every component that is shown, with attribute findings nested beneath
            their variable rather than rendered as components of their own.
        """
        if not self._show_passed:
            findings = [f for f in findings if f.type != FindingType.PASSED]
        if not findings:
            return []

        # A finding's parent only names a nesting home when it differs from its own component
        # -- the bounds check sets both to the bare variable name, rendering as an ordinary row.
        own = [f for f in findings if not f.parent or f.parent == f.component]
        nested = [f for f in findings if f.parent and f.parent != f.component]

        own_by_component = Report(own).grouped_by(lambda finding: finding.component)
        nested_by_parent = Report(nested).grouped_by(lambda finding: finding.parent)

        # Ranked over both buckets, so a variable whose only failure sits in a nested attribute
        # still outranks a WARN-only variable with nothing nested at all.
        def worst_severity(component: str) -> int:
            """Return the lowest (worst) severity rank across a component's own and nested findings.

            Args:
                component: The component name to rank, a key of ``own_by_component`` and/or
                    ``nested_by_parent``.

            Returns:
                The lowest :data:`_SEVERITY` value across both buckets for this component.
            """
            own_findings = own_by_component.get(component, ())
            nested_findings = nested_by_parent.get(component, ())
            return min(_SEVERITY[finding.status] for finding in (*own_findings, *nested_findings))

        ordered = sorted(
            set(own_by_component) | set(nested_by_parent),
            key=lambda component: (worst_severity(component), component),
        )

        lines: list[str] = []
        for component in ordered:
            lines.extend(
                self._render_component_block(
                    component,
                    own_by_component.get(component, []),
                    nested_by_parent.get(component, []),
                    by_finding,
                    depth,
                )
            )
        return lines

    def _render_component_block(
        self,
        component: str,
        own: list[Finding],
        nested: list[Finding],
        by_finding: dict[Finding, DedupedFinding],
        depth: int,
    ) -> list[str]:
        """Render one variable's own findings, then its attribute findings as sub-blocks.

        ``depth`` is this component heading's indent level; its own header/rows sit at
        ``depth + 1``, an attribute sub-heading also at ``depth + 1`` (a sibling of the header,
        not nested under it), and its rows at ``depth + 2``. A variable with nested findings but
        none of its own still gets a heading and header (``own`` may be empty;
        :meth:`_render_grid_block` renders them regardless). No sub-block repeats the header row
        -- the component's own header already covers the whole thing.

        Args:
            component: The variable name this block is about.
            own: This component's own (non-attribute-scoped) findings.
            nested: Attribute-scoped findings whose ``parent`` is this component.
            by_finding: Maps each finding back to its full :class:`DedupedFinding` (count and
                files).
            depth: This component heading's indent level.

        Returns:
            The component's heading, header row and own rows, followed by one sub-heading and
            row group per distinct attribute, sorted by attribute name.
        """
        lines = self._render_grid_block(
            f"{_INDENT * depth}{component}",
            _SECTION_GRID,
            sorted(own, key=_render_order),
            by_finding,
            indent=depth + 1,
        )
        nested_by_attribute = Report(nested).grouped_by(lambda finding: finding.component)
        for attribute_component in sorted(nested_by_attribute):
            suffix = _attribute_heading(attribute_component, component)
            lines.extend(
                self._render_grid_block(
                    f"{_INDENT * (depth + 1)}.{suffix}",
                    _SECTION_GRID,
                    sorted(nested_by_attribute[attribute_component], key=_render_order),
                    by_finding,
                    header=False,
                    indent=depth + 2,
                )
            )
        return lines

    def _render_global_attributes(
        self, findings: list[Finding], by_finding: dict[Finding, DedupedFinding], depth: int
    ) -> list[str]:
        """Render one produced file's global attributes as a single compact block.

        ``depth`` is this block's heading indent level; its header/rows sit at ``depth + 1``. A
        global attribute only ever produces one kind of line (an existence check against the SoS
        rules), so ``source``, ``scope`` and ``check`` are constant across the whole block and
        dropped in favour of just the attribute name, what was found, and its severity.

        Args:
            findings: This file's global-attribute-scoped findings.
            by_finding: Maps each finding back to its full :class:`DedupedFinding` (count and
                files).
            depth: This block's heading indent level.

        Returns:
            The block's heading, header row, and one line per shown attribute -- or ``[]`` when
            there are none, or every one is PASSED and ``show_passed`` is not set (the same
            per-line rule :meth:`_render_components` applies).
        """
        if not self._show_passed:
            findings = [f for f in findings if f.type != FindingType.PASSED]
        if not findings:
            return []

        attribute_width = max(len("attribute"), *(len(finding.component) for finding in findings))
        columns = (
            _Column("attribute", attribute_width, lambda finding: finding.component),
            _Column("found", _FOUND_WIDTH, lambda finding: finding.type),
            _Column("severity", _SEVERITY_WIDTH, lambda finding: finding.status),
        )
        return self._render_grid_block(
            f"{_INDENT * depth}global attributes", columns, findings, by_finding, indent=depth + 1
        )


def _cit_version() -> str:
    """Return the installed ``cit`` version, or a placeholder outside an installed package.

    Returns:
        The ``confluence-contracts`` distribution version, or ``"0+unknown"`` if package
        metadata cannot be found (e.g. a source checkout with no install record).
    """
    try:
        return importlib.metadata.version(_DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


def _sort_key(finding: Finding) -> tuple[int, str, str, str, str, str]:
    """Deterministic ordering key: severity first, then a stable tiebreaker.

    Used both to order the findings within one group and to order deduplicated entries, so
    rendering never depends on dict/set iteration order.

    Args:
        finding: The finding to compute a sort key for.

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


def _render_order(finding: Finding) -> tuple[bool, int, Check, FindingType]:
    """Order findings within a shown block: structure before metadata, then severity/check/type.

    Shared by a component's own findings and its nested attribute sub-blocks, so the two
    validators' takes on one thing never interleave arbitrarily in either place -- without
    changing :func:`_sort_key` itself, which also orders the deduplicated entries and CSV rows.

    Args:
        finding: The finding to compute a sort key for.

    Returns:
        A tuple ordering structure before metadata, then by severity, check and finding type.
    """
    return (
        finding.validation != ValidationSource.STRUCTURE,
        _SEVERITY[finding.status],
        finding.check,
        finding.type,
    )


def _attribute_heading(component: str, parent: str) -> str:
    """The nested sub-heading text for an attribute finding: its component, parent stripped.

    ``removeprefix`` against the explicit ``parent`` field, never a split on ``.`` -- a
    variable's own name may itself contain a dot (``Qmean_momma.constrained``), which a
    dot-split cannot tell apart from a real attribute suffix. When ``component`` and ``parent``
    are equal there is nothing to strip, so the whole component comes back unchanged -- the
    bounds check's bare-component finding, which the caller renders as an ordinary row rather
    than a sub-heading.

    Args:
        component: The attribute finding's own component name (e.g. ``Qmean_momma.units``).
        parent: The variable name to strip as a prefix (e.g. ``Qmean_momma``).

    Returns:
        ``component`` with the ``"{parent}."`` prefix removed, or ``component`` unchanged when
        it does not start with that prefix.
    """
    return component.removeprefix(f"{parent}.")


def _section_heading(source: ValidationSource) -> str:
    """The findings-body heading for one source, e.g. ``Structure checks -- the module contract``.

    Args:
        source: The validation source to build a heading for.

    Returns:
        The heading text for that source's section.
    """
    return f"{source.capitalize()} checks -- {_SECTION_COMPARISON[source]}"


def _checks_block() -> str:
    """Build the legend's "Checks" block from :data:`_CHECKS_BY_SOURCE`.

    Rendered from the same data that names each check, rather than hand-written text, so a
    ninth check added to :class:`Check` cannot silently leave the legend stale -- the assertion
    below fails loudly the first time it is exercised.

    Returns:
        The "Checks" heading, then one ``<source>  <what it compares against>`` line per
        validator, followed by one indented ``<check>  <description>`` line per check.
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


def _grid_header(columns: tuple[_Column, ...]) -> str:
    """Build a grid's header row from its column spec.

    Args:
        columns: The column spec to build a header row for.

    Returns:
        Each column's header text, left-justified to its width and separated by one space, plus
        a trailing ``files`` for the always-unwidened file-count column every grid ends with.
    """
    return " ".join(f"{column.header:<{column.width}}" for column in columns) + " files"


def _grid_row(finding: Finding, columns: tuple[_Column, ...], files_suffix: str) -> str:
    """Build one finding's row from the same column spec its header row used.

    Args:
        finding: The finding to render a row for.
        columns: The column spec, matching the one used to build the header row.
        files_suffix: The already-computed files suffix to append (see :func:`_files_suffix`).

    Returns:
        Each column's value, left-justified to its width and separated by one space, plus the
        files suffix -- trailing whitespace stripped when there is no suffix to show.
    """
    prefix = " ".join(f"{column.value(finding):<{column.width}}" for column in columns)
    return f"{prefix} {files_suffix}".rstrip()


def _grid_continuation(columns: tuple[_Column, ...]) -> str:
    """Compute a grid's files-column indent from the same column spec it was rendered with.

    Deriving it from ``columns`` (rather than a module-level constant sized for one grid) is
    what lets the global-attribute block's narrower spec get its own continuation instead of
    inheriting the component grid's -- the defect a past round introduced.

    Args:
        columns: The column spec the grid was rendered with.

    Returns:
        One space per column width plus one separator per column, landing exactly where that
        grid's ``files`` column starts.
    """
    return " " * (sum(column.width for column in columns) + len(columns))


def _grid_message_offset(columns: tuple[_Column, ...]) -> str:
    """Compute a grid's message indent: the start of its *second* column, from the same spec.

    A mismatch message reads more naturally under the row's own ``check``/``found`` values than
    pushed out to the files column, which a wide grid can put well past 100 characters.

    Args:
        columns: The column spec the grid was rendered with.

    Returns:
        One space per the first column's width, plus one separator.
    """
    return " " * (columns[0].width + 1)


def _files_suffix(entry: DedupedFinding) -> str:
    """Describe how many distinct results files a deduplicated finding was seen in.

    A finding seen in exactly one file names that file's basename instead of the otherwise
    useless ``x1 file``; one with no file at all (e.g. a registry cross-check finding with
    nothing on disk behind it) renders no suffix.

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


def _files_lines(
    entry: DedupedFinding, max_files: int, continuation: str, indent: int = 3
) -> list[str]:
    """Render the indented basenames to list beneath a multi-file finding, capped and sorted.

    A single file is never listed here even when present -- :func:`_files_suffix` already names
    it inline, and listing it again would be a redundant one-line list under every finding.

    Args:
        entry: The deduplicated finding whose files to list.
        max_files: How many basenames to list before truncating.
        continuation: The already-computed indent to the files column (see
            :func:`_grid_continuation`), so a listed basename lines up under it.
        indent: How many indent levels to render each line at.

    Returns:
        One already-indented line per basename (up to ``max_files``, in ``entry.files``' sorted
        order), plus a trailing ``... and N more (use --csv for the full list)`` line when
        truncated. Empty when there are fewer than two non-empty files.
    """
    real_files = [f for f in entry.files if f]
    if len(real_files) < _MIN_FILES_TO_LIST:
        return []

    shown = real_files[:max_files]
    lines = [f"{_INDENT * indent}{continuation}{Path(f).name}" for f in shown]
    remaining = len(real_files) - len(shown)
    if remaining:
        lines.append(
            f"{_INDENT * indent}{continuation}... and {remaining} more "
            "(use --csv for the full list)"
        )
    return lines
