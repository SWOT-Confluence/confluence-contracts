"""Command-line entry point for the cit contract tool."""

import argparse


def _validate(args: argparse.Namespace) -> int:
    raise SystemExit("cit validate: not implemented yet")


def _parse(args: argparse.Namespace) -> int:
    raise SystemExit("cit parse: not implemented yet")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``cit`` argument parser with its subcommands."""
    parser = argparse.ArgumentParser(
        prog="cit",
        description="Validate Confluence module result files against contracts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Check a result file against its contract.")
    validate.set_defaults(func=_validate)

    parse = subparsers.add_parser("parse", help="Generate a draft contract from a result file.")
    parse.set_defaults(func=_parse)

    return parser


def main() -> None:
    """Entry point for the ``cit`` console script."""
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
