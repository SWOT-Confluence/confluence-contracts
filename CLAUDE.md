# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on the
**confluence-contracts** project.

## Project Overview

confluence-contracts ships **CIT** (Confluence Integration Testing): a small, pip-installable
tool exposing the `cit` command that validates the result files produced by SWOT-Confluence
modules against per-module **contracts**. Confluence is a cloud workflow of ~20 decoupled
modules that interoperate **only** through NetCDF/JSON files on a shared mount; nothing today
checks that a changed module still produces the file variables/dtypes/shapes/metadata that
downstream modules (and the PO.DAAC SoS product) expect. `cit` opens a produced file and checks
it against a committed contract — no module execution or pipeline run needed.

The repo is named for the durable artifact (the contract files): both CIT and the future
`confluence_interfaces` package consume the same `contracts/<module>.yml` files. Phase 1 delivers
two schema-level validations that need no golden data — **structural** (variable existence,
dtype, shape) and **metadata-rules** (SoS attribute conformance) — plus a **contract parser**
(`cit parse`) that generates a draft contract *from* a result file. Numeric regression and
science hooks are deferred to later phases.

## Project Structure

```
src/cit/                       # the `cit` import package (flat: one module per concern)
  __init__.py  __main__.py     # `cit` CLI — subcommands: validate | parse
  contract.py  schema.py       # EXPECTED contract models (Pydantic v2) + JSON Schema generation
  netcdf.py    result.py       # low-level NetCDF reader + ACTUAL file read model
  data.py                      # I/O layer (the only component that touches disk)
  validation.py                # structural validator + report-only health checks + cross-check
  rules.py                     # SoS metadata-rules model + validator
  parse.py                     # ContractParser (result -> draft contract; inverse of validate)
  report.py                    # Finding + Report (statuses, banner, exit policy)
  resources/                   # all bundled package data (loaded via importlib.resources)
    contracts/*.yml            # per-module contracts — the interface source of truth
    rules/sos_results_rules.yml  # generated SoS rules artifact
tests/                         # pytest suite
pyproject.toml                 # dependencies (managed with uv) + ruff config
# planned (later issues): schema/contract.schema.json, tools/rules_convert.py, .github/workflows/
```

Most modules are currently docstring-only stubs; each docstring names the classes/behavior its
issue will fill in. Per-module variation lives in the `contracts/*.yml` **data** files, not in
split Python — the design goal is that a new flat module needs zero new code.

## Build & Test

Local development and tooling use **`uv`** (no Docker, no `make`). A plain `uv sync` / `uv run`
installs the `test` group by default (`default-groups = ["test"]`), so `ruff` and `pytest` are
always available.

```bash
uv sync                        # create/refresh the virtualenv (incl. dev tooling)
uv run cit --help              # show the CLI (subcommands: validate, parse)
uv run pytest                  # run the test suite
uv run ruff check              # lint
uv build --wheel               # build the wheel (verify packaged data files if relevant)

# Once implemented (later issues):
uv run cit validate --module <m> --results <path> [--strict]   # validate a result file
uv run cit parse    --module <m> --results <path> -o contracts/<m>.yml   # draft a contract
```

Prefer `uv run <tool>` over a bare `pytest`/`ruff`; a bare command can fall through to a system
or pyenv interpreter that lacks the project's dependencies.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode, causing the agent to hang waiting for y/n input.

```bash
cp -f source dest      # NOT: cp source dest
mv -f source dest      # NOT: mv source dest
rm -f file             # NOT: rm file
rm -rf directory       # NOT: rm -r directory
cp -rf source dest     # NOT: cp -r source dest
```

Other commands that may prompt: `scp`/`ssh` — use `-o BatchMode=yes`; `apt-get` — use `-y`; `brew` — use `HOMEBREW_NO_AUTO_UPDATE=1`.

### Never use `cd <dir> && git ...`

Running git after `cd`-ing into a directory triggers a hardcoded Claude Code safety prompt (changing into a directory can execute untrusted `.git/hooks`). This prompt is **not** controlled by `permissions.allow` — no allowlist entry can suppress it. Always run git against an explicit path instead:

```bash
git -C /abs/path/to/repo status      # NOT: cd /abs/path/to/repo && git status
git -C /abs/path/to/repo log --oneline -5
```

`git -C` runs in that directory without changing the shell cwd, so it matches the `Bash(git:*)` allow rule and runs without prompting. This applies to main Claude as well as subagents.

## Subagents

Use subagents where appropriate — e.g. for parallel research, exploring the codebase, or running independent tasks concurrently. Agent prompt files live in `.claude/agents/`:

- `developer` — implements specific features and tasks using `beads` for issue tracking (see Beads block below and [`.claude/agents/developer.md`](.claude/agents/developer.md))

**MANDATORY**: For any task that involves writing, modifying, or deleting code, you MUST invoke the `developer` subagent rather than implementing directly. Do not write or edit code as the main agent.

## Skills

Project skills live in `.claude/skills/` and are preloaded for the `developer` subagent:

- **`confluence`** — the SWOT-Confluence domain (modules, the shared-mount file interop, reaches, SoS/PO.DAAC), how to author a `contracts/*.yml`, and which artifacts must never be hand-edited. Invoke it before writing or changing a contract, or when reasoning about what a module produces and who consumes it.
- **`changelog`** — `CHANGELOG.md` conventions (one bullet per epic, keepachangelog 1.1.0) and the semver rules keyed to CIT's real breaking surfaces: the contract schema, the CLI, and any check that newly FAILs what previously passed.

## Slash Commands

- `/make-plan <context-path | #issue>` — turn a filled-out context document **or a GitHub issue** into a reviewable development plan written to `docs/plans/`
- `/plan-work <plan-path>` — turn an approved plan into beads epics + sub-tasks, linked to their GitHub issues
- `/start-issue [epic-id]` — claim a ready epic, cut a feature branch off `dev`, and hand off sub-tasks to the `developer` subagent

Suggest these proactively when the user reaches the corresponding step in the workflow (context or issue written → `/make-plan`; plan approved → `/plan-work`; ready to implement → `/start-issue`).

## GitHub issues ↔ beads

Both trackers are in use and they serve different readers. **GitHub issues** are the team-visible record — phase and epic-level work, often a parent issue with sub-issues, readable by collaborators who never run an agent. **beads** is the execution queue — sub-task granularity, dependency edges, `bd ready`.

- One GitHub issue per **epic**; never one per sub-task.
- Every beads epic description opens with a `GitHub: owner/repo#N` line; each linked issue carries one comment naming the bead id.
- **GitHub is authoritative for scope; beads is authoritative for state.** If they disagree about what an epic includes, the issue wins and beads gets corrected — surface the divergence rather than resolving it silently.
- Issues close when their PR merges (`Closes #N` in the PR body), never by hand.
- Search GitHub before creating an issue; a duplicate epic issue splits the discussion thread.

## Git Branching (orchestration-level rules)

- Each beads epic maps to exactly one feature branch off `dev` and one PR; sub-tasks are individual commits on that branch.
- Never commit or push directly to `dev`.
- Every PR that changes something user-visible updates `CHANGELOG.md` under `[Unreleased]` — one bullet per epic (see the `changelog` skill).
- After the human merges, close the epic issue: `bd close <epic-id> --reason "merged <PR URL>"`. The GitHub issue closes itself via the PR's `Closes #N`.
- If the next epic depends on a previous one, pull `dev` before cutting the new branch.

The inner development loop — sub-task commit cadence, quality checks, commit-message format, branch-cutting mechanics — is owned by the `developer` subagent. See [`.claude/agents/developer.md`](.claude/agents/developer.md). Main Claude should orchestrate (find/claim issues, invoke `/start-issue`, delegate to `developer`) and never edit code directly.

## Conventions & Patterns

- **Python ≥ 3.11**, dependencies managed with `uv`. Add **runtime** deps to `[project.dependencies]` and **dev/test** tooling to the `[dependency-groups] test` group in `pyproject.toml` — not via inline installs. (`openpyxl` is dev-only: the rules artifact is generated at dev time, never imported at runtime.)
- **Linting**: ruff (line-length 100), with Google-style docstrings and type annotations enforced — every public module/function/class needs a docstring. See `[tool.ruff.lint]` in `pyproject.toml`. Run `uv run ruff check` before finishing.
- **Generated-artifact integrity**: the following are **generated, committed, and drift-checked in CI** — do not hand-edit them; change the source and regenerate:
  - `schema/contract.schema.json` ← derived from `src/cit/contract.py` via `src/cit/schema.py` (`Contract.model_json_schema()`, serialized deterministically).
  - `src/cit/resources/rules/sos_results_rules.yml` ← generated from the SoS metadata spreadsheet (in the parent `confluence` repo, `docs/sos-dataset/`) by `tools/rules_convert.py` (openpyxl).
- **Contracts are the interface source of truth**: `src/cit/resources/contracts/*.yml` declare each module's produced variables/dtypes/shapes/metadata. Changing a declared interface changes what CIT enforces — only do so when a task explicitly calls for it. Prefer seeding new contracts with `cit parse`, then hand-reviewing.
- **Package data & resources**: all bundled data lives under the `src/cit/resources/` subpackage (`contracts/`, `rules/`), loaded via `importlib.resources.files("cit.resources")` — this resolves identically under editable and installed modes and avoids module-vs-directory name clashes (`rules.py`/`schema.py` next to a `rules/`/`schema/` dir). Keep runtime-loaded data in this subpackage, not scattered next to modules.
- **Minimal changes**: implement only what a task requires; avoid speculative abstractions and unrelated refactors.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->
