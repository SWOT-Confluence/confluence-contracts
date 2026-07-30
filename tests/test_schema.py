"""Tests for JSON Schema generation (cit.schema).

These cover the issue's AC3: schema generation is deterministic, the committed schema on
disk matches freshly rendered output (a drift check), and the dtype Literal survives into
the generated schema. The drift check is skipped until ``schema/contract.schema.json`` is
generated and committed by a later task.
"""

import json

import pytest

from cit.schema import SCHEMA_PATH, build_schema, render_schema


def test_render_schema_is_deterministic():
    """render_schema() produces byte-identical output across repeated calls."""
    assert render_schema() == render_schema()


def test_render_schema_has_trailing_newline():
    """The rendered schema ends with a single trailing newline."""
    rendered = render_schema()

    assert rendered.endswith("\n")


@pytest.mark.skipif(
    not SCHEMA_PATH.exists(),
    reason="schema/contract.schema.json not generated yet — run `uv run python -m cit.schema`",
)
def test_committed_schema_matches_render():
    """The committed schema on disk matches freshly rendered output (drift check)."""
    assert SCHEMA_PATH.read_text() == render_schema(), (
        "schema/contract.schema.json is stale — regenerate with `uv run python -m cit.schema`"
    )


def test_dtype_literal_survives_into_schema():
    """The dtype Literal token 'f8' appears in the generated schema."""
    assert "f8" in json.dumps(build_schema())


def test_str_dtype_token_in_schema():
    """The vlen 'str' dtype token appears in the generated schema enum."""
    assert '"str"' in json.dumps(build_schema())
