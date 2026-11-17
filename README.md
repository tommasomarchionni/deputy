# deputy

> A frontier model orchestrates. Any other model does the legwork, through MCP, and gets
> better at it every week.

[![CI](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml/badge.svg)](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Status:** alpha (M1). The server, installer and checks work; the benchmark and the tuning loop
(M2) are next. Expect breaking changes.

## Quick start

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init                      # your model server: llama.cpp, Ollama, LM Studio, vLLM, ...
deputy doctor                    # check it answers
deputy install claude-code --apply   # or: vscode (Copilot), cursor, codex, generic
deputy instructions --write CLAUDE.md   # teach the agent when to delegate (saves tokens)
```

Full guide: [docs/install.md](docs/install.md).

## What it is

`deputy` is an MCP server plus a set of agent profiles. A *frontier orchestrator* (Claude Code
today; GitHub Copilot and others later) hands small, checkable tasks to *worker models* of any
kind (local llama.cpp, Ollama, OpenAI- or Anthropic-compatible endpoints, hosted APIs). The
server runs the worker inside a sandbox, verifies the result with checks the orchestrator
declares, corrects it when it can, and reports back a structured result with evidence and
metrics. It records what worked, so routing and prompts improve from measurements, not hunches.

## Goals, stated so they can be measured

"Perfect" and "best in the world" are not testable. These are (targets, to be confirmed by the
benchmark in `docs/self-improvement.md`):

- every claim a worker makes about a file is checked by code, not trusted;
- line citations correct >= 98% on the benchmark; zero writes outside the sandbox;
- a worker never gets a shell: checks run in the server, from an allow-list;
- for each model and task class, the router picks the cheapest path that passes the checks;
- the orchestrator spends fewer tokens with deputy than without, per task class, measured and
  reported (`tokens_saved`); a class that does not save is not delegated;
- a change to prompts, profiles or parameters ships only if it beats the baseline on a held-out
  set, with variance accounted for.

## Documents

- `docs/install.md` — install and connect Claude Code, Copilot, Cursor, Codex.
- `docs/configuration.md`, `docs/tools.md`, `docs/security.md` — reference.
- `docs/architecture.md` — roles, tools, execution loop, routing, security, stack.
- `docs/memory.md` — what is remembered (model profiles, lessons, project memory) and why.
- `docs/self-improvement.md` — the improvement loop, the benchmark, the guardrails.
- `docs/roadmap.md` — milestones and what is proven at each.

## In the repository

- `src/deputy/` — the server (`delegate`, `status`, `profiles`, `feedback`), sandbox, checks.
- `src/deputy/profiles/helper.md` — the bundled read-only helper profile.
- `examples/project-dev.md` — a template for a project-specific developer profile.
- `docs/local-serving.md` — measured speed of a local model under different server settings.
- `tools/sandbox.sh` — an isolated working copy, so a worker never writes in the real repo.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Security
issues: [SECURITY.md](SECURITY.md).

## License

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
