"""Contract parser: generate a draft contract *from* a result file (inverse of validation).

The same NetCDF engine as validation, pointed the other way -- where the validator reads a
contract and checks a file, the parser reads a file and writes a contract. Its Phase-1 use
is to seed the first contracts (``momma.yml`` and the rest) from sample ``.nc`` files rather
than authoring them by hand.

Planned (P1-10):

- ``ContractParser.parse(result, module, path_template, rules=None) -> Contract`` -- walk a
  ``Result`` via :mod:`cit.netcdf`, emit each variable's ``dtype`` / ``dimensions`` / ``required``
  and the ``filepath`` template, pre-fill the ``version`` / ``source`` scaffold, and -- when a
  rules artifact is supplied -- merge the SoS ``attrs`` per variable so the draft is complete
  enough to pass both validators. Serializes to draft YAML for ``cit parse``.
"""
