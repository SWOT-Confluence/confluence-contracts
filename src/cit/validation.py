"""Structural validator plus the report-only health checks and registry cross-check.

Compares a contract (EXPECTED, :mod:`cit.models`) against an actual file (ACTUAL,
:mod:`cit.result`) and emits ``Finding``s. This is the core interop guarantee: a changed
module still produces the variables/dtypes/shapes downstream consumers expect.

Planned:

- ``ResultsValidation.compare_contract_results(contract, result) -> list[Finding]`` (P1-6):
  per variable, existence (missing+required -> FAIL, missing+optional -> INFO), dtype match,
  shape/dims match; declared dimensions present; an undeclared file variable -> WARN (drift),
  asymmetric by design.
- ``check_completeness`` / ``check_non_fill`` (P1-16): report-only guards against silent
  partial or all-fill output; they never fail the run, even under ``--strict``.
- ``cross_check(registry) -> list[Finding]`` (P1-17): a files-closed lint -- every declared
  ``consumes`` variable must be produced by some contract, or it FAILs naming the orphan.
"""
