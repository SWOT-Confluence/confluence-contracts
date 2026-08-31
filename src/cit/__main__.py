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


def _pair(value: str) -> tuple[str, str]:
    """Parse a strict ``MODULE=PATH`` tagged value for ``--module-file`` and ``--rule-file``.

    Both flags require an explicit name tag. Bare paths are rejected so the module identity
    of a committed contract is always something the user typed, never inferred from a filename.

    Args:
        value: The raw argument, e.g. ``momma=/mnt/data/flpe/momma/12590000211_momma.nc``
            or ``output=docs/sos-dataset/sos_metadata.xlsx``.

    Returns:
        A ``(name, path)`` pair, with whitespace stripped from both sides of the ``=``.

    Raises:
        argparse.ArgumentTypeError: If the value contains no ``=`` separator, or if either
            the name or the path is empty after stripping whitespace.
    """
    name, sep, tail = value.partition("=")
    name, tail = name.strip(), tail.strip()
    if not sep or not name or not tail:
        raise argparse.ArgumentTypeError(f"expected MODULE=VALUE, got {value!r}")
    return name, tail


class _SingleAction(argparse.Action):
    """Custom action for tagged flags: accumulate ``(name, path)`` pairs, reject duplicates.

    Used by both ``--module-file`` and ``--rule-file``.  Each flag may be repeated once per
    distinct name tag, but a second occurrence of the same name is rejected at parse time with
    a clear error naming the duplicate and the flag.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: tuple[str, str],
        option_string: str | None = None,
    ) -> None:
        """Append ``values`` to the accumulated list, rejecting a repeated name.

        Args:
            parser: The argument parser, used to emit the error.
            namespace: The in-progress parsed namespace.
            values: The ``(name, path)`` pair produced by :func:`_pair`.
            option_string: The option string used on the command line (e.g. ``--module-file``).
        """
        current: list[tuple[str, str]] = getattr(namespace, self.dest) or []
        name, _ = values
        flag = option_string or self.option_strings[0]
        for existing_name, _ in current:
            if existing_name == name:
                parser.error(f"{flag} {name!r} appears more than once")
        setattr(namespace, self.dest, [*current, values])


def _parse(args: argparse.Namespace) -> int:
    """Run ``cit parse``: build a parse plan from tagged module files and rules sources.

    Args:
        args: Parsed CLI arguments (see the ``parse`` subparser in :func:`build_parser`).

    Returns:
        0 once the parse plan has been built (contract and rules bodies are drafted in P1-10).

    Raises:
        SystemExit: If ``--module-file`` was not given, or if the parse cannot be resolved --
            the orchestrator raises a ``ValueError`` for that (e.g. an unregistered rules name),
            translated here so the user sees a message rather than a traceback.
    """
    if not args.module_file:
        raise SystemExit(
            "cit parse: --module-file MODULE=PATH is required (a result file to draft a "
            "contract from; repeat it for each file)."
        )

    # Names are guaranteed unique by _SingleAction; map each to a single-element list.
    module_files: dict[str, list[str]] = {name: [path] for name, path in args.module_file}

    # args.rule_file is [(name, path), ...] or None; convert to {name: path} or None.
    rule_files: dict[str, str] | None = dict(args.rule_file) if args.rule_file else None

    orchestrate = Orchestrate()
    try:
        orchestrate.parse(module_files, rule_files, strict=args.strict)
    except ValueError as error:
        raise SystemExit(f"cit parse: {error}") from error

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
            "Every value is tagged MODULE=PATH; bare paths are rejected.\n"
            "\n"
            "  cit parse \\\n"
            "      --module-file momma=.../flpe/momma/12590000211_momma.nc \\\n"
            "      --module-file metroman=.../flpe/metroman/12590000211_metroman.nc \\\n"
            "      --rule-file   output=docs/sos-dataset/sos_metadata.xlsx"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parse.add_argument(
        "--module-file",
        action=_SingleAction,
        type=_pair,
        default=None,
        metavar="MODULE=PATH",
        dest="module_file",
        help=(
            "A result file to draft a contract from (repeatable, MODULE=PATH required). "
            "Each MODULE name may appear at most once. "
            "The tag is the name the drafted contract is committed under."
        ),
    )
    parse.add_argument(
        "--rule-file",
        action=_SingleAction,
        type=_pair,
        default=None,
        metavar="RULES=PATH",
        dest="rule_file",
        help=(
            "A rules source to merge SoS metadata from (repeatable, RULES=PATH required). "
            "The tag is the registered rules-parser name (e.g. ``output``), which selects the "
            "parser class and the workbook tabs to read. Each name may appear at most once."
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
