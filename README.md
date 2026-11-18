# deputy

> A frontier model orchestrates. Any other model does the legwork, through MCP, and gets
> better at it every week.

[![CI](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml/badge.svg)](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-github%20pages-blue.svg)](https://tommasomarchionni.github.io/deputy/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Status:** alpha (v0.1). The server, installer, checks, benchmark and tuning commands work; routing,
memory and async tasks are next. Expect breaking changes.

## Highlights

- **Fewer tokens for the frontier model.** Four tiny tool schemas (about 1,200 tokens), results
  by reference, checks instead of re-reading, and an estimate of the tokens saved on every run.
- **Quality you can verify.** The worker has no shell and a confined sandbox; every claim is checked
  by code (cited lines, required facts, forbidden leaks, files written, allow-listed commands).
- **Improves itself, safely.** `deputy bench` measures your model, `deputy tune` recommends or, if
  you allow it, applies better settings, and nothing changes without a measurement.
- **Works with any agent and any model.** Claude Code, GitHub Copilot, Cursor, Codex; llama.cpp,
  Ollama, LM Studio, vLLM or any OpenAI-compatible API.
- **Easy to install and keep current.** One command, no `sudo`, releases with provenance
  attestations, `deputy update`.
- **Developed in the open.** Issues, milestones and a public roadmap board; CI, CodeQL and
  property tests on every change.

## Quick start

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init                      # your model server: llama.cpp, Ollama, LM Studio, vLLM, ...
deputy doctor                    # check it answers
deputy install claude-code --apply   # or: vscode (Copilot), cursor, codex, generic
deputy instructions --write CLAUDE.md   # teach the agent when to delegate (saves tokens)
deputy tune                      # measure your model and get better settings (optional)
```

Full documentation: **<https://tommasomarchionni.github.io/deputy/>** (source in `docs/`).

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

## Documentation

Full site: **<https://tommasomarchionni.github.io/deputy/>** (source in `docs/`).

| Topic | Page |
| --- | --- |
| Install, connect Claude Code, Copilot, Cursor, Codex | [install](docs/install.md) |
| Configuration, tuning, updates | [configuration](docs/configuration.md) |
| The four tools and the checks | [tools](docs/tools.md) |
| Security model | [security](docs/security.md) |
| Architecture, memory, self-improvement | [architecture](docs/architecture.md), [memory](docs/memory.md), [self-improvement](docs/self-improvement.md) |
| Measured speed of a local model | [local serving](docs/local-serving.md) |
| Roadmap | [roadmap](docs/roadmap.md) and the [project board](https://github.com/users/tommasomarchionni/projects/3) |

## In the repository

- `src/deputy/` — the server (`delegate`, `status`, `profiles`, `feedback`), sandbox, checks.
- `src/deputy/profiles/helper.md` — the bundled read-only helper profile.
- `examples/project-dev.md` — a template for a project-specific developer profile.
- `docs/local-serving.md` — measured speed of a local model under different server settings.
- `deputy workspace` — disposable copies of a repository, so a worker never writes in the real one.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Security
issues: [SECURITY.md](SECURITY.md).

## License

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
