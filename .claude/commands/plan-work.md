---
description: >
  Plan work from a plan document. If no epics exist for the plan, creates all
  epics with dependencies and defines sub-tasks for the first epic. If epics
  already exist, defines sub-tasks for the next available epic (predecessor
  closed, no sub-tasks yet).
argument-hint: "<plan-file-path>  (required — e.g. docs/phase2-plan.md)"
allowed-tools: Bash(bd *) Bash(gh issue*) Bash(gh api*) Bash(git log*) Bash(git branch*) Read Glob
---

Plan work from a plan document, bootstrapping epics on first run or continuing with the next available epic.

## Inputs

- `$ARGUMENTS` — path to the plan file (e.g. `docs/phase2-plan.md`). Required.

---

## GitHub issues ↔ beads

This repo tracks work in **both** systems, and they serve different readers:

- **GitHub issues** are the team-visible record — what the project is doing, visible to collaborators who never run an agent. Phase and epic-level work lives here, often as a parent issue with sub-issues.
- **beads** is the execution queue — sub-task granularity, dependency edges, `bd ready`, and the state the developer subagent works from.

They are linked, not merged. The rules:

- **One GitHub issue per epic** (never per sub-task — sub-task churn does not belong in a team-visible tracker).
- Every beads epic description carries a `GitHub: owner/repo#N` line as its first line.
- Every linked GitHub issue gets one comment naming the bead id, so a human reading the issue can find the queue.
- **GitHub is authoritative for scope; beads is authoritative for state.** If they disagree about what an epic includes, the issue wins and beads gets corrected. Never silently edit a GitHub issue body to match beads — surface the divergence to the user.
- Never close a GitHub issue from this command. Issues close when their PR merges (`Closes #N`), which `/start-issue` writes into the PR body.

Helper — resolve the repo once and reuse it:

```
gh repo view --json nameWithOwner --jq .nameWithOwner
```

---

## Step 0: Detect mode

Read the plan file:
```
Read <plan-file-path>
```

Then search beads for any open or closed epics that reference this plan file in their description:
```
bd search <plan-file-path>
```
Also run `bd list --status=open --type=feature` and `bd list --status=closed --type=feature` and scan for epics whose description contains the plan file path.

**Two modes:**

- **Bootstrap mode** — no epics found that reference this plan → go to [Bootstrap path](#bootstrap-path).
- **Continuation mode** — epics already exist → go to [Continuation path](#continuation-path).

---

## Bootstrap path

*Use when no epics exist for this plan.*

### B1. Parse all epics from the plan

Read the plan file and extract every epic:
- Title
- Why it exists (problem it solves)
- What it delivers (explicit deliverable list)
- Scope boundary (what is OUT of scope)
- Any sub-task breakdown listed in the plan
- Dependencies on other epics (which epic must close first)
- Its `**GitHub:** #N` line, if `/make-plan` recorded one

Also read the plan's `> Source issue:` header, if present — that is the parent issue this plan came from.

If the plan does not clearly delineate epics, ask the user to clarify before proceeding.

### B1a. Reconcile epics against GitHub

For each epic, decide which of three states it is in:

1. **Already has an issue** — the plan names `#N`. Verify it still exists and is open:
   `gh issue view <N> --json number,title,state,url`. Reuse it.
2. **No issue, but one plausibly exists** — search before creating, so a re-run of this command
   does not duplicate issues someone filed by hand:
   `gh issue list --state all --search "<distinctive words from the epic title>" --json number,title,state,url`
   Show any near-match to the user and ask whether it is the same work.
3. **Genuinely new** — no issue exists.

Carry that determination into B2 so the user approves issue creation, not just bead creation.

### B2. Confirm the epic list with the user

Print the proposed epic list:
```
Plan: <plan-file-path>
Parent issue: <owner/repo#N> — <title>       (if the plan has one)

Proposed epics:
  1. <title> — <one-line rationale>
       GitHub: #7 (exists, open)  |  #12 (possible match — confirm?)  |  none — will create
  2. <title> — <one-line rationale> (depends on #1)
       GitHub: ...
  ...

Dependencies: <list ordered pairs>

This will create: <n> beads epics, <m> GitHub issues.
Proceed? (or describe changes)
```

**Do not create any issues — beads or GitHub — until the user confirms.**
If they request changes, revise and re-show.

### B3. Create the GitHub issues for epics that need one

For each epic in state 3 (genuinely new), create its issue:
```
gh issue create \
  --title="<epic title>" \
  --body="$(cat <<'EOF'
<why this epic exists>

**Delivers:**
- <deliverable>

**Out of scope:** <exclusions>

Plan: <plan-file-path>
EOF
)"
```
Capture the returned issue number and URL.

If the plan has a parent issue, link the new issue beneath it:
```
gh api repos/{owner}/{repo}/issues/<parent>/sub_issues -f sub_issue_id=<new-issue-id> 2>/dev/null \
  || echo "sub-issue link unavailable — note the parent in the body instead"
```
The sub-issues API needs the issue's **node id**, not its number — fetch it with
`gh issue view <N> --json id --jq .id`. If the call fails, fall back to adding a `Part of #<parent>` line to the new issue body; do not abort the run over a failed link.

### B4. Create all epics in beads

For each confirmed epic (in order):
```
bd create \
  --title="<epic title>" \
  --type=feature \
  --priority=2 \
  --description="<full description: why, what, scope boundary, plan file path, GitHub link>"
```
Capture each returned id.

Include in every epic description:
- A `GitHub: owner/repo#N` line **first**, so the link is visible in `bd show` without scrolling.
- A `Plan: <plan-file-path>` line so future runs of `/plan-work` can detect these epics.
- The epic sequence number (e.g. "Epic 1 of 5").

### B4a. Comment the bead id back onto each GitHub issue

So a human reading the issue can find the queue:
```
gh issue comment <N> --body "Tracked in beads as \`<epic-id>\` (plan: \`<plan-file-path>\`). Sub-tasks and dependency state live there; this issue stays the team-visible record and closes when its PR merges."
```
One comment per issue, on creation only — do not re-comment on subsequent runs.

### B5. Wire epic dependencies

For each epic that depends on a predecessor:
```
bd dep add <later-epic-id> <earlier-epic-id>
```

### B6. Plan sub-tasks for the first epic

The first epic (no predecessor) is now the target. Proceed with steps [S1](#s1-read-prior-context) through [S7](#s7-report-back), treating this as the target epic.

---

## Continuation path

*Use when epics already exist for this plan.*

### C1. Identify the target epic

Run `bd list --status=open --type=feature` and filter for epics referencing the plan file.

Walk the dependency chain: the **target epic** is the first open epic whose predecessor epic is **closed** (or the first epic if it has no predecessor).

If no such epic exists (all are either blocked or sub-tasks are already filed), report that and stop.

### C2. Verify no sub-tasks exist yet

Run `bd list --status=open` and check whether any open issues already reference the target epic in their description or have it as a dependency. If sub-tasks already exist, list them and ask the user whether to add more or stop.

Proceed to steps [S1](#s1-read-prior-context) through [S7](#s7-report-back) with the identified target epic.

---

## Shared steps (sub-task planning)

### S1. Read prior context

If the target epic has a predecessor, run `bd show <predecessor-epic-id>` and collect:
- `close_reason` — what was actually delivered
- Closed sub-task ids that belong to it; run `bd show` on each, collect their `close_reason`

Also run:
```
git log --oneline --no-merges -20
```
to confirm what commits are on the current branch, giving a ground-truth picture of what the codebase looks like now.

If this is the first epic (no predecessor), skip this step.

### S2. Read the target epic's scope

Run `bd show <target-epic-id>` and collect the full description:
- **Why** this epic exists
- **What** it delivers (explicit deliverable list)
- **Scope boundary** (what is OUT of scope)
- Any `Plan:` file path referenced in the description
- Any `GitHub:` issue reference in the description

If the plan file path is present, re-read the relevant section:
```
Read <plan-file-path>
```
Focus on the section for this epic and extract the sub-task breakdown if listed.

If a `GitHub:` reference is present, read the issue **and its comment thread** — the thread is where scope gets refined after the plan was written, and it is authoritative:
```
gh issue view <N> --json title,body,state,url,comments
```

If the issue's scope has diverged from the plan file (a deliverable added or dropped in the thread), **stop and surface the divergence** with both versions side by side. Ask which is current before drafting sub-tasks. Do not silently plan from one and ignore the other, and do not edit the issue to match the plan.

### S3. Draft sub-tasks

Using the deliverable list (and plan file section if available), draft one sub-task per discrete, committable unit of work. A typical epic has 3–7 sub-tasks.

Guidelines:
- Each sub-task must be completable in a single commit (one logical change, testable in isolation).
- Order them so earlier sub-tasks leave no broken state (tests must pass after each commit).
- Reference what the **previous epic delivered** in descriptions where relevant.
- Include concrete acceptance criteria (what test, what output, what file changes).
- Title format: `<EpicNum>.<SubtaskNum>: <imperative action>`

### S4. Confirm sub-tasks with the user

Print the proposed sub-task list:
```
Target epic: <id> — <title>

Proposed sub-tasks:
  1. <title> — <one-line rationale>
  2. ...

Dependencies: <list any ordered pairs>

Proceed? (or describe changes)
```

**Do not create any issues until the user confirms.**
If they request changes, revise and re-show.

### S5. Create the sub-task issues

Sub-tasks live in beads only — **do not create a GitHub issue per sub-task.** Sub-task granularity is execution detail; putting it in the team-visible tracker buries the epic-level signal collaborators actually read.

For each confirmed sub-task (in order):
```
bd create \
  --title="<sub-task title>" \
  --type=task \
  --priority=2 \
  --description="<full description with why/what/acceptance criteria, referencing epic id, the epic's GitHub issue, and prior-work context>"
```
Capture each returned id.

Include the epic's `GitHub: owner/repo#N` line in every sub-task description too — the developer subagent has no memory of prior sessions and may need to read the issue for context.

### S6. Wire sub-task dependencies

- Each sub-task depends on the one before it (if ordered):
  ```
  bd dep add <later-id> <earlier-id>
  ```
- The *first* sub-task has no dependency within the epic, but note the epic id in its description.

### S7. Report back

Print:
- Plan file path
- Mode used (Bootstrap or Continuation)
- All epics (Bootstrap: newly created; Continuation: full list with status), each with its GitHub issue number and URL
- Which GitHub issues were **created** vs **reused** — reusing an existing issue is the good outcome; call out creations so the user can spot an accidental duplicate immediately
- Target epic id + title
- All new sub-task ids with titles
- Which sub-task is now ready to start (`bd ready`)
- Suggested next step: `/start-issue <epic-id>`

---

## Rules

- Never invent sub-tasks or epics that the plan document does not describe. If scope is ambiguous, ask before creating.
- Keep descriptions self-contained — the developer subagent has no memory of prior sessions.
- Do NOT push, commit, or modify code — only `bd` and `gh issue` calls.
- Priority defaults to P2 unless the plan or user says otherwise.
- **Search GitHub before creating an issue.** A duplicate epic issue splits the team's discussion thread and is tedious to unpick. When in doubt, show the near-match and ask.
- **One GitHub issue per epic, never per sub-task.**
- **Never close, reopen, or edit the body of a GitHub issue** from this command. Comment only. Issues close when their PR merges.
- If GitHub is unreachable or `gh` is unauthenticated, say so and continue with beads only — record which epics still need issues, and do not block the run.
- Ignore beads `dolt push` SSH warnings — they are expected in this environment.
