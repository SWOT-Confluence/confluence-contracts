"""Generate the committed JSON Schema from the contract models.

The Pydantic models in :mod:`cit.contract` are the source of truth; this module derives a
standards-compliant JSON Schema from them via ``Contract.model_json_schema()`` and
serializes it deterministically so the committed ``schema/contract.schema.json`` is
byte-stable.

CI regenerates the schema and diffs it against the committed file (a drift check), which
keeps the models and the published schema in lockstep. External tooling -- editors, YAML
validators, future ``confluence_interfaces`` consumers -- can then point at the committed
schema without importing this package.
"""

# Standard imports
import json
from pathlib import Path

# Application imports
from cit.contract import Contract

# JSON schema at root of the repository
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "contract.schema.json"


def build_schema() -> dict:
    """Return the JSON schema derived from the Contract model.

    Returns:
        The schema as a plain dict, straight from ``Contract.model_json_schema()``.
    """
    return Contract.model_json_schema()


def render_schema() -> str:
    """Serialize the schema deterministically (byte-stable across runs).

    Returns:
        The schema as indented, key-sorted JSON text, with a trailing newline.
    """
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


def write_schema() -> None:
    """Write the schema file."""
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(render_schema())


def check_drift() -> bool:
    """True if the committed file matches the freshly generated output.

    Returns:
        Whether :data:`SCHEMA_PATH` already holds the current, freshly rendered schema text.
    """
    return SCHEMA_PATH.read_text() == render_schema()


if __name__ == "__main__":
    write_schema()
