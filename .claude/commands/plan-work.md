---
description: >
  Plan work from a plan document. If no epics exist for the plan, creates all
  epics with dependencies and defines sub-tasks for the first epic. If epics
  already exist, defines sub-tasks for the next available epic (predecessor
  closed, no sub-tasks yet).
argument-hint: "<plan-file-path>  (required — e.g. docs/phase2-plan.md)"
allowed-tools: Bash(bd *) Bash(git log*) Bash(git branch*) Read Glob
---

Plan work from a plan document, bootstrapping epics on first run or continuing with the next available epic.

## Inputs

- `$ARGUMENTS` — path to the plan file (e.g. `docs/phase2-plan.md`). Required.

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

If the plan does not clearly delineate epics, ask the user to clarify before proceeding.

### B2. Confirm the epic list with the user

Print the proposed epic list:
```
Plan: <plan-file-path>

Proposed epics:
  1. <title> — <one-line rationale>
  2. <title> — <one-line rationale> (depends on #1)
  ...

Dependencies: <list ordered pairs>

Proceed to create epics? (or describe changes)
```

**Do not create any issues until the user confirms.**
If they request changes, revise and re-show.

### B3. Create all epics

For each confirmed epic (in order):
```
bd create \
  --title="<epic title>" \
  --type=feature \
  --priority=2 \
  --description="<full description: why, what, scope boundary, plan file path reference>"
```
Capture each returned id.

Include in every epic description:
- A `Plan: <plan-file-path>` line so future runs of `/plan-work` can detect these epics.
- The epic sequence number (e.g. "Epic 1 of 5").

### B4. Wire epic dependencies

For each epic that depends on a predecessor:
```
bd dep add <later-epic-id> <earlier-epic-id>
```

### B5. Plan sub-tasks for the first epic

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

If the plan file path is present, re-read the relevant section:
```
Read <plan-file-path>
```
Focus on the section for this epic and extract the sub-task breakdown if listed.

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

For each confirmed sub-task (in order):
```
bd create \
  --title="<sub-task title>" \
  --type=task \
  --priority=2 \
  --description="<full description with why/what/acceptance criteria, referencing epic id and prior-work context>"
```
Capture each returned id.

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
- All epics (Bootstrap: newly created; Continuation: full list with status)
- Target epic id + title
- All new sub-task ids with titles
- Which sub-task is now ready to start (`bd ready`)
- Suggested next step: `/start-issue <epic-id>`

---

## Rules

- Never invent sub-tasks or epics that the plan document does not describe. If scope is ambiguous, ask before creating.
- Keep descriptions self-contained — the developer subagent has no memory of prior sessions.
- Do NOT push, commit, or modify code — only `bd` calls.
- Priority defaults to P2 unless the plan or user says otherwise.
- Ignore beads `dolt push` SSH warnings — they are expected in this environment.
