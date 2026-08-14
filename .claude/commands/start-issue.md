---
description: >
  Start an epic: cut a feature branch off dev, work through every sub-task
  in parallel via developer subagents (one commit per sub-task), close the
  epic, and summarize for PR review. One call = one branch = one PR.
argument-hint: "[epic-id]  (optional — defaults to next ready epic)"
allowed-tools: Bash(bd *) Bash(git status*) Bash(git checkout*) Bash(git pull*) Bash(git branch*) Bash(git log*) Bash(git push*) Bash(git cherry-pick*) Bash(git worktree*) Bash(gh pr*) Bash(gh issue*) Bash(date *)
---

Work an entire epic end-to-end: branch → sub-tasks → summary. All code writing goes through the `developer` subagent — the main agent orchestrates only.

## Steps

### 1. Pick the epic

If `$ARGUMENTS` is non-empty, treat it as the epic id.

Otherwise run `bd ready` and select the top-priority unblocked epic (`--type=feature`). If multiple epics are tied, list them and ask the user to choose. If `bd ready` is empty, report that and stop.

### 2. Inspect the epic

Run `bd show <epic-id>` and surface to the user:
- Title, full description, scope boundary
- Any plan file path referenced — read it if present
- Any `GitHub: owner/repo#N` reference — read the issue and its comments if present:
  `gh issue view <N> --json number,title,body,state,url,comments`
  The comment thread is where scope gets refined after planning. If it has diverged from the beads
  description, surface both and ask which is current **before** cutting a branch — discovering it
  after five sub-tasks have landed is expensive.
- Predecessor epic close context (`bd show <predecessor-id>`, collect `close_reason`)

Record the issue number; it is needed for the PR body in step 10.

Also collect the sub-tasks that belong to this epic:
```
bd list --status=open
```
Filter for issues that reference this epic id in their description or depend on each other in a chain rooted at this epic. If **no sub-tasks exist**, stop and tell the user to run `/plan-work <epic-id>` first — do not proceed without sub-tasks.

Show the user the ordered sub-task list and ask for confirmation before proceeding.

### 3. Verify a clean working tree

```
git status
```
If there are uncommitted changes or the tree is not clean, stop and ask the user to commit or stash before continuing.

### 4. Cut the feature branch off `dev`

```
git checkout dev
git pull --rebase
git checkout -b <slug>
```

Derive `<slug>` from the epic title: lowercase, hyphen-separated, ~4–6 words (e.g. `feature/epic-1-contract-models-schema`). If a branch with that name already exists, ask the user.

Report the branch name to the user.

### 5. Claim the epic

```
bd update <epic-id> --claim
```

Record the start timestamp in beads:

```
bd update <epic-id> --notes "timing.started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

If the epic has a linked GitHub issue, mark it in progress so collaborators see the work has started without having to read the beads queue:

```
gh issue comment <N> --body "Work started on branch \`<branch-name>\` (beads \`<epic-id>\`)."
```

One comment per epic run. Do not edit the issue body, and do not close it — the PR merge does that.

### 6. Build the execution plan

Collect all sub-task ids and their beads dependency edges (from `bd show` on each sub-task). Build a dependency graph, then compute **execution waves** by topological sort: a wave is a maximal set of sub-tasks whose predecessors are all in earlier waves.

Print the execution plan for the user before starting:
```
Execution plan:
  Wave 1 (parallel):   <subtask-id> "<title>",  <subtask-id> "<title>"
  Wave 2 (sequential): <subtask-id> "<title>"
  Wave 3 (parallel):   <subtask-id> "<title>",  <subtask-id> "<title>"
  ...
```

Label each wave based solely on sub-task count: one sub-task → `(sequential)`, two or more → `(parallel)`. Parallel waves launch all their sub-tasks concurrently as separate developer subagents in a single message.

Ask the user to confirm the execution plan before proceeding.

### 7. Execute waves

For each wave, in order:

#### 7a. Prepare context snapshot

Before launching the wave, collect:
- `git log --oneline -10` on the feature branch — the current commit state
- `close_reason` of every sub-task completed so far in this epic
- Predecessor epic's `close_reason` (collected in step 2)

This context snapshot is the same for all sub-tasks launched in this wave.

#### 7b. Claim all sub-tasks in the wave

```
bd update <subtask-id> --claim   # one call per sub-task in the wave
```

#### 7c. Launch the wave

**Parallel wave (2+ sub-tasks):** Send all developer subagent invocations in a **single message** so they run concurrently. Each must use `isolation: "worktree"` so agents work in separate worktrees and do not race on the filesystem.

**Sequential wave (1 sub-task):** Send a single developer subagent invocation with `isolation: "worktree"` — same pattern as parallel waves. The main agent cherry-picks the commit onto the feature branch after the subagent completes.

Each developer subagent prompt must be **fully self-contained** and include:
- Epic id, title, full description, scope boundary, plan file section (if available)
- Context snapshot from step 7a (prior sub-task close_reasons, predecessor close_reason)
- This sub-task's id, title, full description, and acceptance criteria
- The feature branch name
- The git log from step 7a

Plus these instructions:
- Implement exactly what the sub-task description says — no more, no less
- **Do NOT hand-edit generated artifacts** — `schema/contract.schema.json` and `src/cit/resources/rules/sos_results_rules.yml` are generated, committed, and drift-checked; change their source and regenerate. Do not alter a `resources/contracts/*.yml` declared interface (variables/dtypes/shapes/metadata) unless the sub-task description explicitly says to.
- Run quality checks before committing: `uv run ruff check` and `uv run pytest`
- Commit with a message that references the sub-task id
- Close the sub-task in beads: `bd close <subtask-id> --reason="<what was done>"`
- Do **NOT** push and do **NOT** open a PR — the main agent handles that

#### 7d. Merge parallel worktrees (parallel waves only)

**Never `cd` into a worktree to run git.** `cd <dir> && git ...` trips a hardcoded Claude Code safety prompt (a `cd` target can execute untrusted `.git/hooks`) that no `permissions.allow` entry can suppress, and it stalls orchestration. The main agent stays in the repo root the whole time — operate on worktrees and the feature branch with explicit paths instead:
- Read a worktree's commit sha: `git -C <worktree-path> log --oneline -1`
- Cherry-pick / check status: run from the repo root (already cwd) with no `cd`, or `git -C /abs/path/to/repo <cmd>`.

After all subagents in a parallel wave complete, cherry-pick their commits onto the feature branch in sub-task order:
```
git cherry-pick <commit-sha>   # for each worktree commit, in dependency order
```

If a cherry-pick fails due to conflicts, **attempt to resolve automatically**:
- Read the conflicting files and the conflict markers
- Use the sub-task descriptions and `close_reason`s from both agents to understand what each change intended
- Apply both changes correctly (proximity conflicts, added imports, etc. are almost always resolvable this way)
- Stage the resolved files and continue the cherry-pick: `git cherry-pick --continue`

Only escalate to the user if the conflict is **genuinely ambiguous** — meaning two sub-tasks made contradictory logical changes to the same code and there is no correct way to combine them without knowing the intended behavior. In that case, describe the conflict and the two options clearly; do not ask the user to resolve it in the editor.

Once all commits from a wave are cherry-picked, tear down each worktree **and its branch** that was returned by the subagent. The branch must be deleted explicitly — `git worktree remove` deletes only the working directory and leaves the `worktree-agent-*` branch ref behind, which is what accumulates dangling branches over time.

For each worktree in the wave:
```
# Map the worktree path to its branch before removing it.
branch=$(git worktree list --porcelain | awk -v p="<worktree-path>" '$1=="worktree" && $2==p {found=1} found && $1=="branch" {sub("refs/heads/","",$2); print $2; exit}')
git worktree remove <worktree-path>
git branch -D "$branch"            # delete the worktree-agent-* branch now on the feature branch
```
Then prune any stale administrative entries:
```
git worktree prune
```
This is safe to do as soon as the cherry-pick succeeds — the commit is already on the feature branch, so the worktree branch is redundant.

After all cherry-picks succeed, run the test suite to confirm the merged state is clean:
```
uv run pytest
```
If tests fail after a resolved conflict, fix only the mechanical breakage (import paths, renamed symbols, moved modules) before moving to the next wave — do not change test logic, assertions, or intent. If the failure requires changing what a test actually checks, stop and report to the user. New tests may always be added to cover new functionality introduced by a sub-task.

#### 7e. Verify completion

For each sub-task in the wave, run `bd show <subtask-id>` and confirm status is `closed`. If any sub-task is not closed, report the issue to the user and pause — do not start the next wave.

#### 7f. Continue to the next wave, repeating 7a–7e until all waves are done.

### 8. Final worktree-branch sweep

Per-wave cleanup (step 7d) removes each wave's worktree branch as it lands, but interrupted or older runs can leave strays. Before closing the epic, sweep any remaining `worktree-agent-*` branches **whose commits are already contained in the feature branch** (i.e. fully merged). This never drops unmerged work — `git branch -d` (lowercase `-d`) refuses to delete a branch that is not an ancestor of the current branch.

First prune stale worktree admin entries, then delete merged worktree branches:
```
git worktree prune
for b in $(git branch --format='%(refname:short)' | grep '^worktree-agent-'); do
  git branch -d "$b" 2>/dev/null && echo "deleted merged worktree branch: $b" \
    || echo "kept (unmerged) worktree branch: $b"
done
```

Report to the user which branches were deleted and which were kept. If any `worktree-agent-*` branch is **kept** (unmerged), surface it explicitly — it means a sub-task's commit was never cherry-picked onto the feature branch, which is a real gap worth investigating before opening the PR. Do **not** force-delete (`-D`) these strays automatically; let the user decide.

### 8a. Update the changelog

Invoke the `changelog` skill and add or fold this epic's entry under `## [Unreleased]` in `CHANGELOG.md` — **one bullet for the whole epic**, not one per sub-task. If the epic already has a bullet there (a follow-up PR on the same epic), fold the new work into the existing line rather than appending a second.

Skip only if the epic changed nothing user-visible: no change to what `cit` accepts or reports, the contract schema, a bundled `contracts/*.yml` interface, the exit-code policy, or the CLI surface. Say so explicitly in the summary if you skip.

Commit it on the feature branch:
```
git add CHANGELOG.md
git commit -m "Add changelog entry for <epic-id>: <epic title>"
```

### 9. Record end time and close the epic

Record the end timestamp **before** closing the epic (`bd update` does not work on closed issues):
```
bd update <epic-id> --notes "timing.pr_opened: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Parse both timestamps from `bd show <epic-id>` notes and compute elapsed minutes:
- Extract the `timing.started` value and the `timing.pr_opened` value
- Convert both to epoch seconds (macOS: `date -j -f "%Y-%m-%dT%H:%M:%SZ" "<timestamp>" +%s`)
- Elapsed = (pr_opened_epoch − started_epoch) / 60, rounded to nearest minute

Once elapsed is computed, close the epic:
```
bd close <epic-id> --reason="All sub-tasks complete on branch <branch-name>: <one-line summary of what was delivered>"
```

### 10. Push and open a PR

The PR body must contain these four sections, written from the context accumulated during this session (epic description, sub-task close_reasons, commits, test results).

If the epic has a linked GitHub issue, the body must **open** with a closing keyword on its own line so the merge closes the issue automatically:

```
Closes #<N>
```

Use `Closes` only when this PR fully delivers the issue. If it delivers part of it, write `Part of #<N>` instead — a premature `Closes` silently shuts an issue the team still considers open.

**## Overview**
One paragraph summarising what this PR delivers and why. End with an explicit review request: call out the 1–3 things the reviewer should focus on (e.g. the design decision made, the tricky integration point, the area most likely to have edge cases).

**## Key Changes**
Bulleted list of the most important functional changes, grouped by area. For each bullet, reference the commit sha and the sub-task id that produced it so the reviewer can trace intent to code.

**## Tests & Verification**
- Which quality checks were run (`ruff check`, `pytest`) and whether they passed
- Any import-path or symbol fixes applied to tests (mechanical only — no logic changes)
- Any `cit` commands run to verify (e.g. `cit --help`, `cit validate …`, `cit parse …`)
- Whether the generated-artifact drift check passes, if `contract.py` changed:
  `uv run python -c "from cit.schema import check_drift; print(check_drift())"`
- The `CHANGELOG.md` entry added (or an explicit note that the epic was not user-visible)

**## Implementation**
- Duration: <elapsed> minutes (<timing.started> → <timing.pr_opened>)
- Sub-tasks: <count> tasks across <count> waves

Push the feature branch and create a PR against `dev`:
```
git push -u origin <branch-name>
gh pr create --base dev --title "<epic title>" --body "..."
```

After the PR is created, print a brief summary to the user:
```
Epic complete: <epic-id> — <title>
GitHub issue: #<N> — will close on merge  (or "none linked")
Branch: <branch-name>
PR: <pr-url>
Duration: <elapsed> minutes  (<timing.started> → <timing.pr_opened>)
Changelog: <the bullet added, or "skipped — no user-visible change">
Sub-tasks landed:
  ✓ <subtask-id>: <title> — <close_reason>
  ✓ ...
```

Do **not** close the GitHub issue here. The `Closes #N` in the PR body closes it on merge; closing it now would mark the work done while the PR is still under review.

## Rules

- Never write or edit code directly from the main agent — all implementation goes through the `developer` subagent. The `CHANGELOG.md` entry in step 8a is the one exception: it is a record of the epic, written from context only the orchestrator holds.
- **GitHub issues close on merge, never by hand.** Comment to signal progress; let `Closes #N` in the PR body do the closing. Use `Part of #N` when the PR delivers only part of an issue.
- If GitHub is unreachable or `gh` is unauthenticated, continue — push the branch, report the PR command the user should run, and note which issue link was skipped. Do not block an otherwise-complete epic on it.
- **Artifact integrity is non-negotiable**: generated committed artifacts (`schema/contract.schema.json`, `src/cit/resources/rules/sos_results_rules.yml`) and the declared interfaces in `resources/contracts/*.yml` must not be changed unless that change is the explicit purpose of the sub-task. If a subagent's close_reason mentions hand-editing a generated artifact or altering a contract interface that was not in scope, flag it to the user before continuing.
- Never skip a sub-task or reorder them without asking the user.
- Never push to `dev` — the feature branch is the only push target.
- **Parallel launches**: always use `isolation: "worktree"` for sub-tasks running in the same wave — never launch two developer subagents concurrently on the same working tree.
- **Parallel launches**: always send all subagent calls for a wave in a **single message** — this is what makes them run concurrently. Sending them in separate messages makes them sequential.
- If any subagent in a wave fails or leaves its sub-task open, pause and report before attempting the next wave — do not cherry-pick partial wave results.
- If a cherry-pick conflict occurs after a parallel wave, resolve it automatically using the sub-task descriptions and close_reasons. Only escalate to the user if two sub-tasks made genuinely contradictory logical changes with no correct way to combine them.
- Keep each developer invocation prompt fully self-contained — it has no memory of prior sub-tasks unless you include their close_reason in the prompt.
- Ignore beads `dolt push` SSH warnings — they are expected in this environment.
