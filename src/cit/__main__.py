"""Command-line entry point for the cit contract tool."""

import argparse
import logging

from cit.schema import check_drift

logger = logging.getLogger("cit")


def _validate(args: argparse.Namespace) -> int:
    if args.check:
        if check_drift():
            logger.info("Schema check passed, no drift detected.")
        else:
            logger.info("Schema check did NOT pass, drift detected.")


def _parse(args: argparse.Namespace) -> int:
    raise SystemExit("cit parse: not implemented yet")


def _configure_logging(verbose: bool) -> None:
    """Configure logging: INFO by default, DEBUG when --verbose is set."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``cit`` argument parser with its subcommands."""
    parser = argparse.ArgumentParser(
        prog="cit",
        description="Validate Confluence module result files against contracts.",
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="Enable DEBUG logging (default: INFO).")

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Check a result file against its contract.")
    validate.set_defaults(func=_validate)
    validate.add_argument("-c", "--check", action="store_true", help="Check for drift in JSON schema")

    parse = subparsers.add_parser("parse", help="Generate a draft contract from a result file.")
    parse.set_defaults(func=_parse)

    return parser


def main() -> None:
    """Entry point for the ``cit`` console script."""
    parser = build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
