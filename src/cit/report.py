"""Findings and reporting: the shared output vocabulary for every check.

Defines the single ``Finding`` type that all validators emit and the ``Report`` that
aggregates them -- kept separate from the ``Result`` read model so "a validator's output"
and "an actual file" are never confused.

``Finding`` and its two enums have landed: a finding pairs a ``FindingType`` (what was found)
with a ``FindingStatus`` (how it bears on the exit code), so severity can be re-weighted --
e.g. by ``--strict`` -- without changing what a validator reports.

Planned (P1-9):

- ``Report`` -- collects findings, groups them by file/reach, prints a version banner
  (confluence version + branch/commit) and a separate run-report section, and applies the
  exit policy: any ``FAIL`` -> exit 1; ``WARN``-only -> 0; ``--strict`` promotes rule
  ``WARN``s to ``FAIL``s. The report-only status used by the P1-16 health checks (which never
  change the exit code, even under ``--strict``) joins ``FindingStatus`` with those checks.
"""

from dataclasses import dataclass
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
    """

    FAIL = "FAIL"
    WARN = "WARN"
    INFO = "INFO"


_SEVERITY = {  # TODO evaluate if this is needed
    FindingStatus.FAIL: 0,
    FindingStatus.WARN: 1,
    FindingStatus.INFO: 2,
}
# findings.sort(key=lambda f: _SEVERITY[f.status])


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
        filepath: The produced file the finding came from.
        message: Optional detail, e.g. the disagreeing contract and result values.
    """

    type: FindingType
    status: FindingStatus
    module_name: str
    component: str
    filepath: str
    validation: str
    message: str = ""


class Report:
    """Aggregate findings and apply the run's exit policy (stub — behavior lands in P1-9)."""

    def __init__(self, findings: list[Finding]) -> None:
        """Store the findings to aggregate.

        Args:
            findings: The check outcomes collected across a validation run.
        """
        self._findings = findings
