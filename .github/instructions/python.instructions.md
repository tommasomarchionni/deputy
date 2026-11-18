---
description: Writing the product code in src/deputy
applyTo: 'src/**'
paths: 'src/**'
---

# Product code (`src/deputy`)

- Python 3.11+, `from __future__ import annotations`, full type hints; `pyright` is strict on `src/`.
- Clean code: a function does one thing and has a name that says it; extract instead of nesting;
  no flag arguments that switch behaviour; no comment that restates the code, only the reason.
- Prefer polymorphism over `if`/`elif` chains: a new check is a new class with `evaluate`, a new
  command is a new module with `register`. Do not edit a chain of conditions.
- Pydantic models for data that crosses a boundary (config, tool arguments, results); plain
  dataclasses inside.
- Errors: a small exception type per module (`SandboxError`, `ConfigError`, ...) whose message is safe
  to show to the user or the model. Catch narrowly; never `except Exception`.
- Security invariants: workers get only `read_file`, `write_file`, `grep`, confined with `realpath`;
  secrets come from a mode-600 file or the environment, never from config, traces or prompts; commands
  run only from the policy allow-list, without a shell.
- Token economy: the MCP tool schemas stay tiny (`tests/test_public_surface.py` and the size test).
  Results carry references and verdicts, not file contents.
- Everything in English, including error messages and docstrings.
