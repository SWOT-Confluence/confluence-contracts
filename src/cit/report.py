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
    """"""
    ...


class Report:
    """"""

    def __init__(self, findings: list[Finding]) -> None:
        """"""
        self._findings = findings