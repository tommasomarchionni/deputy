# deputy

**A frontier model orchestrates. Any other model does the legwork, through MCP. Every answer is
checked by code, and your frontier tokens are saved.**

deputy is an MCP server. Your agent (Claude Code, GitHub Copilot, Cursor, Codex, anything that
speaks MCP) hands small, checkable, read-heavy tasks to a cheaper model (a local llama.cpp or Ollama
model, or any OpenAI-compatible API). The server runs the worker inside a sandbox, verifies the
answer with checks you declare, corrects it when it can, and tells the agent exactly what it can
trust.

## Why

| Goal | How |
| --- | --- |
| **Fewer tokens for the frontier model** | Five tiny tool schemas (about 1,200 tokens), results by reference, checks instead of re-reading, and a measured `orchestrator_tokens_saved_est` on every run. |
| **Quality you can verify** | The worker has no shell and a confined sandbox. Every claim is checked by code: cited lines, required facts, forbidden leaks, files written, allow-listed commands. |
| **Measured, then tuned** | `deputy bench` measures your model on a built-in suite; `deputy tune` recommends (or applies, if you allow it) request settings. Nothing changes without a measurement. Learning from feedback is planned: [how deputy improves](self-improvement.md). |

## Get started

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init
deputy doctor
deputy install claude-code --apply     # or: vscode, cursor, codex, generic
deputy instructions --for claude --apply
```

Then read [Install and connect an agent](install.md).

## Status

Alpha. The server, installer, checks, benchmark and tuning commands work and are tested; the next work is real-app verification and the offline optimiser: see the [roadmap](roadmap.md) and
[how deputy improves](self-improvement.md) for what exists today.
