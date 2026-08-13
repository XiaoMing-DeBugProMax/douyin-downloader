# Project continuation handoff

This document is the entry point for a fresh AI conversation. It is a map to the
authoritative project state, not a second issue tracker. Rebuild live progress
from GitHub and Git before acting.

## Start every fresh conversation here

1. Read `AGENTS.md` completely.
2. Confirm the checkout and remote state:

   ```powershell
   git status -sb
   git branch --show-current
   git log --oneline --decorate -20
   git fetch origin
   git rev-list --left-right --count main...origin/main
   ```

3. Read the latest approved parent specification and live tickets, including
   comments and blocking edges:

   ```powershell
   gh issue view 1 --comments
   gh issue list --state open --limit 50
   gh issue view <candidate-number> --comments
   ```

4. Read the product and domain sources listed below before designing or coding.
5. Verify that every blocker on the candidate ticket is closed. Do not select a
   ticket from its number or this document alone.
6. Run the most relevant focused baseline test before modifying behavior.

If GitHub or the remote cannot be reached, report that limitation and use the
last fetched refs only for local inspection. Do not claim that remote issue or
branch state is current.

## Source-of-truth order

When sources disagree, use this order and raise any unresolved contradiction to
the user:

1. `AGENTS.md` for non-negotiable project, security, privacy, test, and build
   rules.
2. GitHub parent specifications and their child ticket bodies, comments, state,
   and blocking edges for approved scope and live progress. GitHub Issues are
   the canonical tracker; see `docs/agents/issue-tracker.md`. Issue #1 and
   tickets #2 through #14 are the completed Cycle 1 record, not an open backlog.
3. `docs/superpowers/specs/2026-07-30-douyin-local-archive-foundation.md` for
   the accepted Cycle 1 archive-foundation baseline, while
   `docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md` remains
   the protected quick-download baseline.
4. `CONTEXT.md` for domain vocabulary and `docs/adr/` for architectural
   decisions. Follow the consumer rules in `docs/agents/domain.md`.
5. Git history, tests, and the current implementation for evidence of what has
   actually landed.
6. `docs/research/` for cited technical findings. Research informs decisions but
   does not override an approved spec or ADR.

Implementation plans under `docs/superpowers/plans/` are execution aids, not
live status records. They may be absent or older than the current code.

## Required Matt Pocock workflow

The repository-level setup has already been completed. Do **not** rerun
`/setup-matt-pocock-skills` unless the user wants to change the issue tracker,
triage labels, or domain-document layout.

Use only the Matt Pocock workflow and the skills it routes to. Do not invoke a
Superpowers skill, plugin, command, or workflow unless the user explicitly
authorizes it for the current task. The historical `docs/superpowers/` path is
not such authorization.

Use `/ask-matt` when the correct Matt route is uncertain. For this project,
apply the following routes:

### Existing approved frontier ticket

For an existing `ready-for-agent` ticket whose blockers are closed:

1. Fetch and read the full ticket body and comments.
2. Read relevant specs, `CONTEXT.md`, ADRs, and prior tests.
3. Use `/implement` directly; do not recreate the accepted spec or tickets.
4. Follow TDD at the highest pre-agreed behavioral seam: establish a failing
   test, implement the smallest complete change, and refactor while green.
5. Run focused tests regularly, type checking regularly, and the full project
   gate once at the end.
6. Use `/code-review` against a fixed base on both axes: Standards and Spec.
   Evaluate findings technically, fix accepted blockers test-first, and rerun
   verification.
7. Commit the completed work on its feature branch and present the standard
   integration choices below unless the user already authorized one.

### New or materially changed multi-session scope

When the user introduces a new substantial scope rather than selecting an
approved ticket:

1. Use `/grill-with-docs` only when decisions still need to be sharpened and
   recorded in the domain docs or ADRs.
2. Use `/to-spec` to synthesize the settled conversation into a spec, agree on
   the highest practical test seam, and publish the spec to GitHub with the
   configured triage label.
3. Use `/to-tickets` to draft tracer-bullet vertical slices and their blocking
   edges. Obtain user approval of granularity and dependencies before publishing
   one GitHub Issue per ticket.
4. Start a fresh conversation for each `/implement` ticket when practical,
   working blockers-first.

Do not substitute a generic brainstorming or planning flow for a required Matt
phase. Other skills may support research, diagnosis, prototyping, domain
modeling, or verification when they are genuinely needed, but they do not
replace `/to-spec`, `/to-tickets`, `/implement`, or `/code-review`.

## Stable checkpoint as of 2026-08-14

- Local and remote `main` were synchronized at `6100751` after the unified
  Python environment and verification scripts were added.
- Cycle 1 parent Issue #1 and implementation tickets #2 through #14 are closed
  as completed. All 114 child-ticket acceptance items were checked, integrated
  linearly into `main`, and formally accepted.
- Cycle 1 delivered the protected quick-download baseline plus managed archive
  outputs, persistent three-level tasks, pause/cancel/restart/repair, the local
  archive library, location and Recycle Bin lifecycle, SQLite recovery, system
  tray continuation, five-workspace UI integration, packaging gates, and real
  Windows UAT.
- The final Cycle 1 evidence is in Issue #1's single progress comment and
  `docs/test-reports/2026-08-13-cycle-1-windows-uat.md`.
- The latest integrated full gate reported 361 passed and 2 skipped, with Ruff
  and strict mypy passing for 32 source files.
- There is no approved next implementation ticket. Treat any further expansion
  as new scope: use the Matt `/to-spec` and `/to-tickets` path and obtain user
  approval before publishing or implementing it.
- The ignored `.tmp/` directory is user-owned local state. Do not inspect,
  stage, delete, or clean it unless the user explicitly requests that action.

This checkpoint is historical evidence. GitHub and Git remain authoritative for
all later progress.

## Project boundaries that must survive every ticket

- Preserve the original quick-download behavior and its regression coverage.
- Bind the local server only to `127.0.0.1`; never add LAN or public exposure.
- Never read existing browser/Douyin login cookies or log secrets, authorization
  data, or media URLs. Follow the approved authorization ADRs for any future
  step-up capability.
- Keep remote media URLs in memory only and out of frontend responses, SQLite,
  metadata, backups, and logs.
- Keep archive writes beneath a user-authorized root and preserve traversal,
  symlink, and Windows reparse-point defenses.
- Preserve `1.txt`, `2.txt`, and `AI项目开发总提示词.md`.
- Evolve the existing frontend and single-process application; do not introduce
  a second backend or replace the approved workspace structure silently.

## Verification and completion

Use the repository scripts from `AGENTS.md`. They locate the Git-common-dir
shared `.venv`, keep imports pointed at the current worktree, and avoid repeated
interpreter discovery:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Preflight
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Focused <pytest-node>
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Full
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

If the shared environment is absent, run `scripts/bootstrap-dev.ps1` with the
full Python 3.12 path when needed. If it exists but is unusable, rerun with
`-Repair`; never clear it implicitly. `verify.ps1` is read-only and must not
install or repair dependencies.

Before reporting completion:

1. Read the full command output and confirm zero failures.
2. Recheck every acceptance criterion in the selected GitHub Issue.
3. Confirm `git status` contains no accidental or unrelated changes.
4. Keep issue updates factual: include commit range and verification evidence.
5. Do not push, close issues, create PRs, or alter parent issues without explicit
   user authority. Selecting standard integration option 1 grants authority for
   that issue's complete merge/push/status/progress-comment sequence only.

After an issue branch is committed and green, present exactly these supported
paths:

1. Merge locally to `main`, push the issue, update its acceptance/state, and
   update the parent issue's single progress comment when applicable.
2. Push and create a Pull Request.
3. Keep the current branch for later.
4. Discard the work after resolving the exact safe cleanup target.

For option 1, perform the authorized sequence continuously: recheck/fetch the
remote, merge without rewriting history, push, update the child issue, then edit
the one existing parent progress comment rather than creating duplicates. If any
step fails, stop and report the exact partial state.

## Suggested opening message for the next conversation

> Continue the project in `D:\workplace\douyin`. Read `AGENTS.md` and
> `docs/agents/handoff.md` completely, then rebuild live progress from GitHub
> Issues and Git. Cycle 1 is complete, so do not invent a next ticket. Use only
> the Matt Pocock workflow; do not use Superpowers unless I explicitly authorize
> it. For new expansion scope, start with `/to-spec` and `/to-tickets`. Do not
> push, close issues, create a PR, or edit parent progress unless I authorize a
> standard integration path.
