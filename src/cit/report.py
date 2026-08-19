"""Findings and reporting: the shared output vocabulary for every check.

Defines the single ``Finding`` type that all validators emit and the ``Report`` that
aggregates them -- kept separate from the ``Result`` read model so "a validator's output"
and "an actual file" are never confused.

``Finding`` and its enums have landed: a finding pairs a ``FindingType`` (what was found) with a
``FindingStatus`` (how it bears on the exit code), so severity can be re-weighted -- e.g. by
``--strict`` -- without changing what a validator reports.

``Report`` has also landed (P1-9.2): it aggregates findings, deduplicates them for display (see
:meth:`Report.deduplicated`), groups them along an arbitrary axis (see :meth:`Report.grouped_by`),
and applies the exit policy (see :attr:`Report.exit_code`) -- any ``FAIL`` fails the run, a
``WARN``-only or empty run passes, and ``REPORT`` findings never affect the exit code, even under
``--strict``.

Planned (P1-9, remaining):

- Rendering (9.3): a version banner (confluence version + branch/commit) and a run-report
  section, replacing today's bare aggregation with an actual ``Report.__str__``.
- CSV export (9.4) of every raw finding.
- CLI wiring (9.5): ``cit validate``/``cit parse`` and the locked ``print(report);
  sys.exit(report.exit_code)`` shape.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum


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

    def __init__(self, findings: list[Finding]) -> None:
        """Store the findings to aggregate.

        Args:
            findings: The check outcomes collected across a validation run.
        """
        self._findings = findings

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
