"""Generate the committed JSON Schema from the contract models.

The Pydantic models in :mod:`cit.models` are the source of truth; this module derives a
standards-compliant JSON Schema from them via ``Contract.model_json_schema()`` and
serializes it deterministically so the committed ``schema/contract.schema.json`` is
byte-stable.

CI regenerates the schema and diffs it against the committed file (a drift check), which
keeps the models and the published schema in lockstep. External tooling -- editors, YAML
validators, future ``confluence_interfaces`` consumers -- can then point at the committed
schema without importing this package.
"""
