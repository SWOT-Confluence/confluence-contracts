"""Command-line entry point for the cit contract tool."""

import argparse
import logging
import sys
from pathlib import Path
from typing import NoReturn

from cit.orchestrate import Orchestrate
from cit.report import DEFAULT_MAX_FILES, ValidationSource

_CHECKS_ALL = "all"

logger = logging.getLogger("cit")


def _validate(args: argparse.Namespace) -> int:
    """Run ``cit validate``: stream a run mount's results against their contracts.

    Args:
        args: Parsed CLI arguments (see the ``validate`` subparser in :func:`build_parser`).

    Returns:
        The report's exit code: 1 if any finding is FAIL, else 0.

    Raises:
        SystemExit: If ``--results`` was not given -- none of the CLI's arguments are
            ``required=True`` (so ``cit validate`` alone still parses), so this combination is
            validated here instead, with a clear, actionable message rather than a traceback.
    """
    if not args.results:
        raise SystemExit(
            "cit validate: --results MOUNT is required (the run mount root, e.g. the directory "
            "containing flpe/momma/)."
        )
    if args.max_files is not None and args.max_files < 1:
        raise SystemExit(
            f"cit validate: --max-files must be at least 1 (got {args.max_files})."
        )

    show_files = args.show_files or args.max_files is not None
    max_files = DEFAULT_MAX_FILES if args.max_files is None else args.max_files
    checks = None if args.checks == _CHECKS_ALL else ValidationSource(args.checks)
    orchestrate = Orchestrate(args.results)
    report = orchestrate.run(
        strict=args.strict,
        modules=args.module,
        show_passed=args.show_passed,
        show_files=show_files,
        max_files=max_files,
        checks=checks,
    )

    text = str(report)
    print(text)  # noqa: T201 -- the report is the tool's stdout product, not a log line

    if args.report:
        Path(args.report).write_text(text + "\n")
    if args.csv:
        report.write_csv(args.csv)

    return report.exit_code


def _parse(args: argparse.Namespace) -> int:
    """Run ``cit parse`` (not yet implemented; see P1-10).

    Args:
        args: Parsed CLI arguments.

    Raises:
        SystemExit: Always -- the ``parse`` subcommand is not implemented yet.
    """
    raise SystemExit("cit parse: not implemented yet")


def _configure_logging(verbose: bool) -> None:
    """Configure the ``cit`` logger only: INFO by default, DEBUG when --verbose is set.

    Deliberately does not call ``logging.basicConfig`` (which configures the *root* logger) --
    that would also turn on INFO-level chatter from third-party loggers (netCDF4, pydantic) that
    propagate to root. Attaching a handler directly to the ``cit`` logger and leaving
    ``propagate`` at its default keeps this run's diagnostics on stderr without affecting any
    other logger's terminal output.

    Args:
        verbose: When True, log at DEBUG; otherwise INFO.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``cit`` argument parser with its subcommands.

    ``-v``/``--verbose`` is defined on two separate ``parents=`` parsers so both
    ``cit -v validate`` and ``cit validate -v`` parse and set ``args.verbose``: the top-level
    parser's copy defaults to ``False`` (so ``args.verbose`` always exists), while each
    subparser's copy defaults to ``argparse.SUPPRESS`` -- when a subcommand's ``-v`` is *not*
    given, argparse's subparser-defaults merge (which otherwise unconditionally overwrites the
    parent namespace with the subparser's own defaults) leaves ``verbose`` untouched, so a
    ``-v`` given before the subcommand survives.

    Returns:
        The configured top-level parser, with ``validate`` and ``parse`` subparsers attached.
    """
    top_shared = argparse.ArgumentParser(add_help=False)
    top_shared.add_argument(
        "-v", "--verbose", action="store_true", help="Enable DEBUG logging (default: INFO)."
    )

    sub_shared = argparse.ArgumentParser(add_help=False)
    sub_shared.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Enable DEBUG logging (default: INFO).",
    )

    parser = argparse.ArgumentParser(
        prog="cit",
        description="Validate Confluence module result files against contracts.",
        parents=[top_shared],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="Check a result file against its contract.",
        parents=[sub_shared],
    )
    validate.add_argument(
        "--module",
        action="append",
        default=None,
        metavar="MODULE",
        help="A module to validate (repeatable). Defaults to every bundled contract.",
    )
    validate.add_argument(
        "--results",
        default=None,
        metavar="MOUNT",
        help="The run mount root holding the results tree (e.g. containing flpe/momma/).",
    )
    validate.add_argument(
        "--strict",
        action="store_true",
        help="Treat SoS metadata-rule warnings as failures.",
    )
    validate.add_argument(
        "--checks",
        choices=[*list(ValidationSource), _CHECKS_ALL],
        default=_CHECKS_ALL,
        help=(
            "Render only this section of the report (default: all). Rendering-only -- the exit "
            "code, counts line, and --csv always reflect every check, regardless of --checks."
        ),
    )
    validate.add_argument(
        "--show-passed",
        action="store_true",
        dest="show_passed",
        help="Also render components whose findings are all PASSED.",
    )
    validate.add_argument(
        "--show-files",
        action="store_true",
        dest="show_files",
        help="List the result files behind each finding (truncated; see --max-files).",
    )
    validate.add_argument(
        "--max-files",
        type=int,
        default=None,
        dest="max_files",
        metavar="N",
        help=(
            "Cap how many result files are listed per finding "
            f"(default: {DEFAULT_MAX_FILES}). Implies --show-files."
        ),
    )
    validate.add_argument(
        "--report",
        default=None,
        metavar="PATH",
        help="Also write the human-readable report text to PATH.",
    )
    validate.add_argument(
        "--csv",
        default=None,
        metavar="PATH",
        help="Also write every raw finding to PATH as CSV, one row per occurrence.",
    )
    validate.set_defaults(func=_validate)

    parse = subparsers.add_parser(
        "parse",
        help="Generate a draft contract from a result file.",
        parents=[sub_shared],
    )
    parse.set_defaults(func=_parse)

    return parser


def main() -> NoReturn:
    """Entry point for the ``cit`` console script.

    Exits with the exit code returned by the dispatched subcommand handler (e.g. a
    :class:`~cit.report.Report`'s :attr:`~cit.report.Report.exit_code` for ``validate``).
    """
    parser = build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
