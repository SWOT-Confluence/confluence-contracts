"""Command-line entry point for the cit contract tool."""

import argparse
import logging
import sys
from pathlib import Path
from typing import NoReturn

from cit.orchestrate import Orchestrate
from cit.parse import DEFAULT_RULE_NAME, RulesParser
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
        raise SystemExit(f"cit validate: --max-files must be at least 1 (got {args.max_files}).")

    show_files = args.show_files or args.max_files is not None
    max_files = DEFAULT_MAX_FILES if args.max_files is None else args.max_files
    checks = None if args.checks == _CHECKS_ALL else ValidationSource(args.checks)
    orchestrate = Orchestrate()
    report = orchestrate.validate(
        args.results,
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


def _module_file(value: str) -> tuple[str | None, str]:
    """Parse a ``--module-file`` value, which may name its module explicitly.

    ``PATH`` alone is the everyday form -- the module is matched from the filename against the
    groups the rules source declares. ``MODULE=PATH`` states it outright, for a file whose name
    does not carry its module or a module the rules source does not know.

    Args:
        value: The raw argument, e.g. ``/mnt/data/flpe/momma/12590000211_momma.nc`` or
            ``momma=/mnt/data/somewhere/results.nc``.

    Returns:
        An ``(explicit_module_name_or_None, path)`` pair.
    """
    name, separator, tail = value.partition("=")
    if separator and "/" not in name and name.strip() and tail.strip():
        return name.strip(), tail.strip()
    return None, value.strip()


def _parse(args: argparse.Namespace) -> int:
    """Run ``cit parse``: resolve each result file to its module and its metadata rules.

    Args:
        args: Parsed CLI arguments (see the ``parse`` subparser in :func:`build_parser`).

    Returns:
        0 once every result file has been resolved to a module.

    Raises:
        SystemExit: If ``--module-file`` was not given, or if the parse cannot be resolved --
            the orchestrator raises a ``ValueError`` for that, translated here so the user sees
            a message rather than a traceback.
    """
    if not args.module_file:
        raise SystemExit(
            "cit parse: --module-file PATH is required (a result file to draft a contract "
            "from; repeat it for each file)."
        )

    orchestrate = Orchestrate()
    try:
        targets = orchestrate.parse(
            args.module_file, args.rule_file, args.rules, strict=args.strict
        )
    except ValueError as error:
        raise SystemExit(f"cit parse: {error}") from error

    for target in targets:
        if target.rules is None:
            source = "no rules"
        elif target.module == target.rules.rule_name:
            source = f"{type(target.rules).__name__}: whole file (root + every group tab)"
        else:
            source = f"{type(target.rules).__name__}: {target.module} tab"
        logger.info(
            "%-12s <- %s  [%s]",
            target.module,
            ", ".join(path.name for path in target.module_files),
            source,
        )
    return 0


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
        epilog=(
            "A module's name, the SoS group it writes, and the workbook tab holding that "
            "group's metadata are all the same string, so none of them is a separate "
            "argument: name the result files and the workbook, and the rest follows.\n"
            "\n"
            "  cit parse \\\n"
            "      --module-file .../flpe/momma/12590000211_momma.nc \\\n"
            "      --module-file .../flpe/metroman/12590000211_metroman.nc \\\n"
            "      --rule-file docs/sos-dataset/sos_metadata.xlsx"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parse.add_argument(
        "--module-file",
        action="append",
        type=_module_file,
        default=None,
        metavar="PATH",
        dest="module_file",
        help=(
            "A result file to draft a contract from (repeatable). Its module is matched from "
            "the filename against the groups the rules source declares; write MODULE=PATH to "
            "state it outright."
        ),
    )
    parse.add_argument(
        "--rule-file",
        default=None,
        metavar="PATH",
        dest="rule_file",
        help=(
            "The SoS metadata workbook supplying every module's metadata -- one workbook for "
            "the whole run, with one tab per module. Omit it to draft contracts with no SoS "
            "metadata merged in."
        ),
    )
    parse.add_argument(
        "--rules",
        default=DEFAULT_RULE_NAME,
        choices=RulesParser.names(),
        metavar="NAME",
        help=(
            "Which rules parser reads --rule-file (default: %(default)s). This names the rules "
            f"*source*, not a module: {', '.join(RulesParser.names())}."
        ),
    )
    parse.add_argument(
        "--strict",
        action="store_true",
        help="Treat a parse with no --rule-file as an error rather than a warning.",
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
