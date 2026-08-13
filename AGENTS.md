# Project Rules

- Cycle 1 is complete. Its accepted product baseline is defined by `docs/superpowers/specs/2026-07-30-douyin-local-archive-foundation.md`; the approved original single-video baseline remains in `docs/superpowers/specs/2026-07-28-douyin-local-downloader-design.md`. Neither may regress. New expansion scope requires an approved GitHub specification and tickets.
- Keep the server bound to `127.0.0.1`; never add LAN/public binding.
- Never read browser/Douyin login cookies or log secrets/media URLs.
- Preserve `1.txt`, `2.txt`, and `AI项目开发总提示词.md`.
- Before continuing expansion work in a fresh conversation, read `docs/agents/handoff.md` and rebuild current progress from GitHub Issues and Git rather than relying on chat history.
- Use only the Matt Pocock engineering flow and the Matt skills it routes to for expansion work. Do not invoke a Superpowers skill, plugin, command, or workflow unless the user explicitly authorizes Superpowers for the current task. A document living under `docs/superpowers/` is historical project input, not authorization to use that workflow.
- Repository setup is already complete: do not rerun `/setup-matt-pocock-skills` unless the issue tracker, triage vocabulary, or domain-doc layout changes. Use `/to-spec` and `/to-tickets` for new or materially changed multi-session scope; use `/implement` for an approved frontier ticket, with TDD and `/code-review` as required by that skill. Do not replace a required Matt phase with an unrelated planning workflow.
- Use the repository scripts as the canonical Python entry points. Do not rediscover Python ad hoc or install dependencies during verification.
- Environment check: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Preflight`
- Focused test: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Focused <pytest-node>`
- Full gate: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Full`
- Create or explicitly repair the shared environment with `scripts/bootstrap-dev.ps1`; verification itself must remain read-only.
- Build: `powershell -ExecutionPolicy Bypass -File scripts/build.ps1`
- After an issue is implemented, reviewed, verified, and committed, present the four standard integration choices: (1) merge locally to `main`, push, update the issue acceptance/state, and update the parent issue's single progress comment when applicable; (2) push and create a Pull Request; (3) keep the branch; (4) discard the work. Selecting option 1 authorizes that complete in-scope sequence, but never a force-push or unrelated issue changes.

## Agent skills

### Continuation handoff

Read `docs/agents/handoff.md` before selecting or implementing the next issue.

### Issue tracker

Issues and PRDs are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-role triage label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This repository uses a single-context domain documentation layout. See `docs/agents/domain.md`.
