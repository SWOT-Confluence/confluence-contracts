---
name: confluence
description: Domain context for SWOT-Confluence, how to author a contracts/*.yml, and which artifacts must never be hand-edited. Use when writing or changing a contract, touching a generated artifact, or reasoning about what a module produces and who consumes it.
---

## The domain

**Confluence** is a cloud workflow of ~20 decoupled modules that estimate river discharge from
SWOT satellite observations. Modules never call each other — they interoperate **only** through
NetCDF/JSON files on a shared mount. A module reads its inputs from the mount, writes its outputs
back, and the next module picks them up.

That file boundary is the entire interface, and nothing in the workflow enforces it. A module can
rename a variable, change a dtype, or drop a dimension and the pipeline keeps running until a
downstream module fails on data that is already hours old — or, worse, until a wrong number
reaches the published product. **CIT exists to make that boundary checkable without running the
pipeline.**

Orientation:

- **Granularity.** Work is partitioned by *reach*, *set*, *basin*, and *continent*. A module
  typically writes one file per reach (`flpe/momma/{reach_id}_momma.nc`); the reach/set/basin/
  continent JSON manifests are execution *inputs* that drive orchestration, not module *results*.
- **FLPE** modules (momma, neobam, hivdi, metroman, sad, sic4dvar, …) each estimate discharge with
  a different algorithm. They produce the same *kind* of output with different variables.
- **SoS** (SWORD of Science) is the aggregated product published to **PO.DAAC**. Its metadata
  conventions are what `rules.py` lints — this is why attribute correctness is a first-class
  check, not a nicety.
- **SWORD** is the river-network database modules read reach geometry from.
- Dimension *sizes* vary per run (`nt` is a reach's timestep count). Dimension *names and order*
  are the interface.

Terms map directly onto the code: EXPECTED = the contract (`contract.py`), ACTUAL = the produced
file (`result.py`), and a `Finding` (`report.py`) is one disagreement between them.

## Authoring a contract

Contracts live in `src/cit/resources/contracts/<module>.yml` and are **the interface source of
truth** — both CIT and the future `confluence_interfaces` package read them. Changing a declared
interface changes what CIT enforces, so only do it when a task explicitly calls for it.

**Seed, then review — do not hand-author.** Run `cit parse` against a real result file to draft
the contract, then hand-review every field. A generated draft describes one sample file; your
review is what turns it into a promise.

A contract declares `version` (the Confluence version it targets), `source` (repo, owner, branch,
commit, image tag — the provenance anchoring it to a functional module version), and per produced
file a `filepath` template, its `dimensions`, and its `variables`.

Per variable:

- `dtype` — a token from the `DataType` literal (`f4`, `f8`, `i4`, `i8`, `S1`, `str`), not a numpy
  dtype string.
- `dimensions` — the dimension **names, in order**. Never sizes: they vary per run, and netCDF
  derives a variable's shape from its dimensions, so matching names in order is sufficient. Order
  matters — `[nx, nt]` and `[nt, nx]` index differently for a consumer.
- `required` — `true` unless a module legitimately omits the variable on some runs. Missing +
  required is a FAIL; missing + optional is a WARN.
- `attrs` — the SoS metadata block, linted against the rules artifact.

Models use `extra="forbid"`, so an unexpected key is an error rather than a silent typo. Validate
a hand-edit with `uv run pytest` before committing.

When adding a variable to an existing contract, prefer `required: false` first if producers may
lag — declaring it required immediately turns every not-yet-updated module into a failing run.

## Never hand-edit generated artifacts

These are generated, committed, and drift-checked in CI. Change the source and regenerate:

| Artifact | Source | Regenerate with |
|---|---|---|
| `schema/contract.schema.json` | `src/cit/contract.py` | `uv run python -m cit.schema` |
| `src/cit/resources/rules/sos_results_rules.yml` | the SoS metadata spreadsheet in the parent `confluence` repo (`docs/sos-dataset/`) | `tools/rules_convert.py` (openpyxl, dev-time only) |

Any change to `contract.py` requires regenerating the schema in the same commit, or CI's drift check
fails. Verify with `uv run python -c "from cit.schema import check_drift; print(check_drift())"`.

All bundled data lives under the `src/cit/resources/` subpackage and is loaded via
`importlib.resources.files("cit.resources")` — this resolves identically in editable and installed
modes, and avoids the `rules.py`/`rules/` and `schema.py`/`schema/` module-vs-directory clash.
Keep runtime-loaded data there, not next to the modules that read it.
