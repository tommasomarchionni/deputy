# deputy

**A frontier model orchestrates. Any other model does the legwork, through MCP, and gets better at
it every week.**

deputy is an MCP server. Your agent (Claude Code, GitHub Copilot, Cursor, Codex, anything that
speaks MCP) hands small, checkable, read-heavy tasks to a cheaper model (a local llama.cpp or Ollama
model, or any OpenAI-compatible API). The server runs the worker inside a sandbox, verifies the
answer with checks you declare, corrects it when it can, and tells the agent exactly what it can
trust.

## Why

| Goal | How |
| --- | --- |
| **Fewer tokens for the frontier model** | Four tiny tool schemas (about 1,200 tokens), results by reference, checks instead of re-reading, and a measured `orchestrator_tokens_saved_est` on every run. |
| **Quality you can verify** | The worker has no shell and a confined sandbox. Every claim is checked by code: cited lines, required facts, forbidden leaks, files written, allow-listed commands. |
| **Gets better by itself, safely** | `deputy bench` measures your model on a built-in suite; `deputy tune` recommends (or applies, if you allow it) settings. Nothing changes without a measurement. |

## Get started

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init
deputy doctor
deputy install claude-code --apply     # or: vscode, cursor, codex, generic
deputy instructions --write CLAUDE.md
```

Then read [Install and connect an agent](install.md).

## Status

Alpha. The server, installer, checks, benchmark and tuning commands work and are tested; routing,
memory and asynchronous tasks are on the [roadmap](roadmap.md).
