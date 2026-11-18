# Copilot instructions for deputy

deputy is an MCP server that lets a frontier model delegate small, checkable, read-heavy tasks to
another model, in a sandbox, with server-run checks. Read `AGENTS.md` first: it has the commands, the
map of the repository and the rules that never bend.

Short version:

- Everything is written in English (code, comments, docs, tests, commit messages).
- Python 3.11+, `uv`, `ruff`, `pyright` strict on `src/`, `pytest`. Clean code: small functions with
  one job, intention-revealing names, polymorphism instead of long `if` chains.
- The worker model has no shell and cannot leave its sandbox; never weaken that.
- Keep the five tool schemas tiny (a test caps their size).
- A bug gets a test that fails before the fix. Unit tests never use the network.
- Docs say what works today; mark planned work as planned.
- Work starts from an issue; PR title `type(area): subject`; body `Closes #N`.

Per-area rules are in `.github/instructions/` and apply by file path.
