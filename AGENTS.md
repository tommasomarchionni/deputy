# AGENTS.md

Guidance for AI coding agents (Claude Code, GitHub Copilot, Codex, Cursor, ...) working on this
repository. Humans: see CONTRIBUTING.md; it is the same process.

## What this is

deputy is an MCP server. A frontier model (the orchestrator) hands small, checkable, read-heavy tasks
to another model (the worker); the server runs the worker in a sandbox, verifies the answer with
checks the orchestrator declares, and reports a structured result. Three goals, in this order of
conflict resolution: **security and quality** (every claim checked by code), **fewer tokens for the
orchestrator** (tiny tool schemas, results by reference), **measured self-improvement** (`bench`,
`tune`). Everything is written in English: code, comments, docs, tests, messages, commits.

## Commands

```sh
uv sync                                  # environment with the dev tools
uv run ruff check . && uv run ruff format --check .
uv run pyright                           # strict on src/
uv run pytest --cov                      # all tests, random order, no network (coverage gate 85%)
uv run pytest tests/integration          # real HTTP, real subprocess, real MCP protocol
uv run pytest --snapshot-update          # ONLY when the public surface changed on purpose
uv run --only-group docs mkdocs build --strict
deputy doctor / bench / tune             # on a real model (needs a model server)
```

Do not run what is not needed: a focused test while editing, the full suite once before you push.

## Map

- `src/deputy/` the product: `server.py` (five MCP tools), `agent.py` (the loop), `sandbox.py`,
  `checks.py`, `backends.py`, `config.py`, `bench.py` and `tune.py`, `updates.py`, `install.py`,
  `cli/` (one module per command group), `profiles/` (bundled worker profiles).
- `tests/` unit, property, snapshot (`test_public_surface.py`), `integration/`, `ci/` (automation
  rules), `live/` (real model, skipped by default).
- `tools/ci/` scripts the workflows run (PR title, labels, release planner); `docs/` the site.

## Rules that never bend

- **The worker has no shell** and cannot leave its sandbox. Never weaken the sandbox, the check
  allow-list, secret handling or the "worker output is a draft" rule to make something pass.
- **Five tools, tiny schemas.** A test caps the schema size; adding a tool or a field needs a reason
  in terms of tokens saved.
- **Claims must be true today.** Docs and READMEs say what works now and mark what is planned.
- No secrets, private code or personal data anywhere (issues, tests, traces, benchmarks).
- Do not add dependencies, workflows or permissions without saying why in the pull request. Pin every
  GitHub Action to a full commit SHA; keep `permissions` minimal.

## How we work

Issue first (`status: ready`), one issue per pull request, branch `<type>/<N>-<slug>`, title
`type(area): subject`, body `Closes #N`, and for `feat` and `fix` a test, or the line
`Regression: none - <reason>`. `feat`, `fix`
and `perf` must change `src/deputy/` or `install.sh`. Report what was verified and what was not;
never invent results. Details: CONTRIBUTING.md ("How the project is run").

## Per-area rules

They load by themselves when you open a file of the area: GitHub Copilot reads
`.github/instructions/*.instructions.md`, Claude Code reads `.claude/rules/` (links to the same
files). Each rule lives in one place; change it there, with a pull request.
