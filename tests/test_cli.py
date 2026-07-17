"""Tests for the cit command-line interface."""

import pytest

from cit.__main__ import build_parser


@pytest.mark.parametrize("command", ["validate", "parse"])
def test_parser_accepts_subcommand(command):
    args = build_parser().parse_args([command])
    assert args.command == command
