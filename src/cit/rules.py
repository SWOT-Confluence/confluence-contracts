"""SoS metadata-rules model and validator (the RULES side).

Loads the committed rules artifact (``cit/resources/rules/sos_results_rules.yml``, generated from
``docs/sos-dataset/sos metadata.xlsx`` by a dev-time converter) and checks metadata
attributes against the SoS specification.

Planned ``RulesValidation.validate_rules(target, module, strict=False)`` runs in two modes:

- lint a ``Contract`` -- every declared variable must carry SoS-compliant ``attrs``;
- check an aggregated SoS/Output result file's variable and global attributes.

Checks include: required global attributes present; per-variable required attributes
present; non-empty ``units``; ``coverage_content_type`` in the ISO codelist;
``valid_min <= valid_max``; ``_FillValue`` matching the fill value for its type. With
``strict=True`` violations FAIL; otherwise they WARN.
"""


class Rule:
    """One SoS metadata rule (stub — fields and checks land in P1-15)."""
