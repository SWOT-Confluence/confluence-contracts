"""Parsers: generate a draft contract *from* a result file, and rules *from* the SoS workbook.

Two inverses of validation live here, behind one :class:`Parser` base. Where the validator reads
a contract and checks a file, :class:`ContractParser` reads a file and writes a contract; where
the validator reads a rules artifact and lints attributes, a :class:`RulesParser` reads the SoS
metadata workbook and writes that artifact.

Identity is one name -- the **module name** -- shared by every side of a parse:

- the contract it drafts (``contracts/<module>.yml``, whose ``module.name`` is that name),
- the :class:`RulesParser` subclass registered under it (``output`` -> :class:`OutputRulesParser`),
- and, for rules, the SoS group whose workbook tab supplies that module's metadata.

The :class:`ParsePlan` returned by :meth:`cit.orchestrate.Orchestrate.parse` is the object graph
for a single parse run: one :class:`ContractParser` per module, one :class:`RulesParser` per rules
source, and a :attr:`ParsePlan.both` view of where the two sets intersect.

Planned (P1-10):

- ``ContractParser.parse`` -- walk the result file via :mod:`cit.netcdf`, emit each variable's
  ``dtype`` / ``dimensions`` / ``required`` and the ``filepath`` template, pre-fill the
  ``version`` / ``source`` scaffold, and -- when a rules artifact is supplied -- merge the SoS
  ``attrs`` per variable so the draft is complete enough to pass both validators.
- ``RulesParser.parse`` -- read this parser's workbook tabs into a
  :class:`~cit.rules.MetadataRules`. openpyxl is a dev-only dependency, so a subclass must
  import it *inside* ``parse``, never at module scope: ``cit validate`` has to keep working in
  an install that never sees the workbook.
"""

import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, get_args

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from cit.data import load_yaml, match_result_filename
from cit.contract import Contract, DataType, ModuleContract, Produces, Source, VariableAttrs, VariableContract
from cit.result import NetcdfResult


logger = logging.getLogger(__name__)   # "cit.orchestrate" — child of "cit"
_REPO_CONFIG = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RepoConfig(BaseModel):
    """One module's hand-maintained parse inputs: its path template and provenance."""

    model_config = _REPO_CONFIG

    filepath: str
    source: Source


class Parser(ABC):
    """Abstract base for anything that reads a source artifact and emits a model."""

    @abstractmethod
    def parse(self) -> BaseModel:
        """Read this parser's source and return the model it describes.

        Returns:
            The parsed model -- a :class:`~cit.contract.Contract` or a
            :class:`~cit.rules.MetadataRules`, depending on the subclass.
        """
        ...

    @staticmethod
    def write(data: BaseModel | dict, output: str | Path, header: str = "") -> None:
        """Serialize a parsed model to YAML at ``output``.

        Keys are emitted in declaration order rather than sorted, matching
        ``tools/rules_convert.py`` -- both write generated, committed artifacts, and a stable
        field order is what makes their diffs reviewable.

        Args:
            data: The model (or plain mapping) to serialize.
            output: Where to write the YAML; parent directories are created as needed.
            header: Comment block to emit above the document, e.g. a do-not-hand-edit banner.
        """
        payload = data.model_dump(mode="json", exclude_none=True) if isinstance(data, BaseModel) else data
        document = yaml.dump(payload, default_flow_style=False, allow_unicode=True, sort_keys=False)
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + document, encoding="utf-8")


class ContractParser(Parser):
    """Draft a contract from one exemplar result file the module produced.

    Exactly one file, deliberately: the caller picks the exemplar and owns finalizing it, so a
    contract is never merged from several of a module's result files -- CIT does not make those
    merge decisions on the caller's behalf.
    """

    def __init__(self, module: str, module_file: Path, repo_config: Path, version: str) -> None:
        """Bind this parser to the module and its result file.

        Args:
            module: The module name -- the identity the drafted contract is committed under.
            module_file: The produced result file to draft a contract from.
        """
        self.module = module
        self.module_file = Path(module_file)
        self.repo_config = repo_config
        self.version = version

    def parse(self) -> None:
        """Read the result file and return the draft contract describing it.

        Returns:
            The drafted :class:`~cit.contract.Contract` (P1-10).
        """
        # Get source configuration data
        repo_config = RepoConfig.model_validate(load_yaml(self.repo_config))
        if match_result_filename(repo_config.filepath, self.module_file.name) is None:
            logger.warning(
                "%s: --module-file %r does not match the %r template declared in %s; the drafted "
                "contract will not discover files like it",
                self.module, self.module_file.name, repo_config.filepath, self.repo_config,
            )

        # Read in module file to NetCDF class
        with NetcdfResult(str(self.module_file)) as result:
            produces = Produces(
                filepath=repo_config.filepath,
                dimensions=list(result.dimensions),
                variables=self._variables(result),
            )

        # Create contract
        contract = Contract(
            version = self.version,
            source = Source.model_validate(repo_config.source),
            module=ModuleContract(
                name=self.module,
                produces=[produces],
                consumes=[]
            )
        )

        output_file = Path(__file__).resolve().parent / "resources" / "contracts" / f"{self.module}.yml"
        self.write(data=contract, output=output_file)

    def _variables(self, result: NetcdfResult) -> dict[str, VariableContract]:
        """"""
        variables = {}
        for name, info in result.variables.items():
            if info.dtype not in get_args(DataType):
                logger.warning("skipping %r: dtype %r is not in the contract vocabulary %s",
                                name, info.dtype, get_args(DataType))
                continue

            variables[name] = VariableContract(
                dtype=info.dtype,
                dimensions=list(info.dims),
                required=True,
                attrs=self._attrs(name, result.variable_attributes.get(name, {})),
            )
        return variables

    def _attrs(self, var_name: str, result_attrs: dict) -> VariableAttrs | None:
        """"""
        fields = {k: v for k, v in result_attrs.items() if k in VariableAttrs.model_fields}
        fields.setdefault("long_name", var_name)
        try:
            return VariableAttrs(**fields)
        except ValidationError as error:
            logger.warning("%s: dropping attrs -- %s", var_name, error)
            return None


class RulesParser(Parser, ABC):
    """Abstract base for one kind of rules artifact, registered by its rule name.

    A subclass declares two pieces of class-level data and nothing else is wired by hand:
    :attr:`rule_name` (the registry key, equal to the module name it governs) and
    :attr:`artifact` (the bundled YAML filename it generates).

    The artifact filename is class data rather than a convention derived from ``rule_name``:
    the one artifact shipped today is ``sos_results_rules.yml`` under ``rule_name`` ``output``,
    so any ``<rule_name>_rules.yml`` rule would already be wrong.

    Registration happens in ``__init_subclass__``, which means a subclass only exists once its
    module has been imported. Keep subclasses in this module; if they ever move into a package,
    import them explicitly (or walk it) so :meth:`names` cannot silently go short.

    Attributes:
        rule_name: The registry key -- the module name whose rules this parser generates.
        artifact: The bundled YAML filename under ``cit/resources/rules/``.
    """

    rule_name: ClassVar[str] = ""
    artifact: ClassVar[str] = ""
    _registry: ClassVar[dict[str, type["RulesParser"]]] = {}

    def __init__(self, rule_file: Path) -> None:
        """Bind this parser to the rules source it reads.

        Args:
            rule_file: Path to the SoS metadata workbook (``docs/sos-dataset/sos_metadata.xlsx``).
        """
        self.rule_file = Path(rule_file)

    @abstractmethod
    def groups(self) -> list[str]:
        """Every module group this rules source describes, sorted.

        This is the set a module name is resolved against, so a parse can name its modules from
        the rules source itself instead of taking them on the command line.

        Returns:
            The group names, each of which is also the name of the module that writes it.
        """
        ...

    def __init_subclass__(cls, **kwargs) -> None:
        """Register a concrete subclass under its ``rule_name``.

        Args:
            **kwargs: Forwarded to :meth:`object.__init_subclass__`.

        Raises:
            TypeError: If a concrete subclass sets no ``rule_name``, or reuses one already
                registered to another parser.
        """
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        if not cls.rule_name:
            raise TypeError(f"{cls.__name__} must set a class-level rule_name")
        if cls.rule_name in RulesParser._registry:
            raise TypeError(f"rule_name {cls.rule_name!r} already registered to "
                            f"{RulesParser._registry[cls.rule_name].__name__}")
        RulesParser._registry[cls.rule_name] = cls

    @classmethod
    def names(cls) -> list[str]:
        """Every registered rule name, sorted -- feeds argparse choices.

        Returns:
            The registered rule names in sorted order.
        """
        return sorted(cls._registry)

    @classmethod
    def get(cls, rule_name: str) -> type["RulesParser"] | None:
        """Return the parser class registered under ``rule_name``, or None.

        Args:
            rule_name: The module name to look up.

        Returns:
            The registered :class:`RulesParser` subclass, or ``None`` when the module has no
            rules parser -- which is the normal case for a module whose contract carries no SoS
            metadata.
        """
        return cls._registry.get(rule_name)

    @classmethod
    def create(cls, rule_name: str, rule_file: Path) -> "RulesParser":
        """Instantiate the parser registered under ``rule_name``.

        Args:
            rule_name: Which rules source to read -- the registry key, not a module name.
            rule_file: Path to the rules source this parser should read.

        Returns:
            The registered subclass, constructed with its rules source.

        Raises:
            ValueError: If no parser is registered under ``rule_name``. The message names every
                registered alternative, since the set is small and fixed at import time.
        """
        parser = cls.get(rule_name)
        if parser is None:
            known = ", ".join(cls.names()) or "none"
            raise ValueError(
                f"no rules parser is registered as {rule_name!r} (registered: {known})"
            )
        return parser(rule_file)


class OutputRulesParser(RulesParser):
    """Read the SoS metadata workbook -- the rules source behind the Output SoS product.

    Named for the artifact it describes, not for a module it is limited to. The Output module
    aggregates every other module's results into the single SoS product, so this one workbook
    holds one tab per group in that product, and each of those tabs is the metadata for the
    module that writes it. Parsing ``momma`` and parsing ``metroman`` therefore both go through
    this parser; they just read different tabs of the same workbook.

    :attr:`fixed_tabs` are read on every run and are not module groups: ``root`` declares the
    SoS file itself, ``global_attributes`` and ``fill_values`` apply file-wide.
    """

    rule_name = "output"
    artifact = "sos_results_rules.yml"

    fixed_tabs: ClassVar[tuple[str, ...]] = ("root", "global_attributes", "fill_values")

    #: Non-group tabs that document the workbook rather than describe data.
    doc_tabs: ClassVar[tuple[str, ...]] = ("README",)

    def groups(self) -> list[str]:
        """Every module-group tab in the workbook, sorted.

        openpyxl is a dev-only dependency, so it is imported here rather than at module scope:
        ``cit validate`` must keep working in an install that never sees the workbook.

        Returns:
            The workbook's tab names, minus :attr:`fixed_tabs`, minus the documentation tabs,
            minus any ``_``-prefixed tab (the workbook's own convention for a template).

        Raises:
            ValueError: If openpyxl is not installed, which is what reading a workbook needs.
        """
        try:
            import openpyxl
        except ImportError as error:  # pragma: no cover -- openpyxl ships in the test group
            raise ValueError(
                "reading a --rule-file workbook needs openpyxl, which is not installed"
            ) from error

        workbook = openpyxl.load_workbook(self.rule_file, read_only=True)
        skip = {*self.fixed_tabs, *self.doc_tabs}
        return sorted(
            tab for tab in workbook.sheetnames if tab not in skip and not tab.startswith("_")
        )

    def parse(self) -> BaseModel:
        """Read the workbook's fixed tabs and module groups into a rules model.

        Returns:
            The parsed :class:`~cit.rules.MetadataRules` (P1-10).
        """
        ...


@dataclass(frozen=True)
class ParsePlan:
    """The object graph for a single ``cit parse`` run.

    Holds one :class:`ContractParser` per module and one :class:`RulesParser` per rules source.
    The :attr:`both` property names the modules that appear in both sets -- those whose drafted
    contract will carry SoS metadata merged from the rules source in step 3 of the parse
    algorithm (P1-10).

    Attributes:
        contracts: A :class:`ContractParser` per module, keyed by module name.
        rules: A :class:`RulesParser` per rules source, keyed by the registered rules name.
    """

    contracts: dict[str, ContractParser]
    rules: dict[str, RulesParser]

    @property
    def both(self) -> list[str]:
        """Module names present in both :attr:`contracts` and :attr:`rules`, sorted.

        Returns:
            The sorted intersection of the contracts and rules key sets.
        """
        return sorted(set(self.contracts) & set(self.rules))
