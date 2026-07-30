"""Findings and reporting: the shared output vocabulary for every check.

Defines the single ``Finding`` type that all validators emit and the ``Report`` that
aggregates them -- kept separate from the ``Result`` read model so "a validator's output"
and "an actual file" are never confused.

Planned (P1-9):

- ``Finding`` -- one check outcome: ``check``, ``target``, ``variable``, a ``status`` of
  ``PASS`` / ``FAIL`` / ``WARN`` / ``INFO`` / ``REPORT``, and a message.
- ``Report`` -- collects findings, groups them by file/reach, prints a version banner
  (confluence version + branch/commit) and a separate run-report section for ``REPORT``
  findings, and applies the exit policy: any ``FAIL`` -> exit 1; ``WARN``-only -> 0;
  ``--strict`` promotes rule ``WARN``s to ``FAIL``s; ``REPORT`` never changes the exit code.
"""

from dataclasses import dataclass


@dataclass
class Finding:
    """One check outcome emitted by a validator (stub — fields land in P1-9)."""

    ...


class Report:
    """Aggregate findings and apply the run's exit policy (stub — behavior lands in P1-9)."""

    def __init__(self, findings: list[Finding]) -> None:
        """Store the findings to aggregate.

        Args:
            findings: The check outcomes collected across a validation run.
        """
        self._findings = findings
