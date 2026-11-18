# deputy

> A frontier model orchestrates. Any other model does the legwork, through MCP. Every answer is
> checked by code, and your frontier tokens are saved.

[![CI](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml/badge.svg)](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-github%20pages-blue.svg)](https://tommasomarchionni.github.io/deputy/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Status:** alpha. The server, installer, checks, benchmark and tuning commands work; project
memory works; background tasks work; routing is next. Expect breaking changes.

## Highlights

- **Fewer tokens for the frontier model.** Five tiny tool schemas (about 1,200 tokens), results
  by reference, checks instead of re-reading, and an estimate of the tokens saved on every run.
- **Quality you can verify.** The worker has no shell and a confined sandbox; every claim is checked
  by code (cited lines, required facts, forbidden leaks, files written, allow-listed commands).
- **Measured, then tuned.** `deputy bench` measures your model and `deputy tune` recommends or, if you
  allow it, applies better request settings; nothing changes without a measurement. (Learning from
  feedback and routing are planned; see [how deputy improves](docs/self-improvement.md).)
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
deputy instructions --for claude --apply   # teach the agent when to delegate (also: copilot, cursor, agents)
deputy tune                      # measure your model and get better settings (optional)
```

Full documentation: **<https://tommasomarchionni.github.io/deputy/>** (source in `docs/`).

## What it is

`deputy` is an MCP server plus a set of agent profiles. A *frontier orchestrator* (Claude Code;
GitHub Copilot, Cursor and Codex are configured but not yet verified in their apps) hands small,
checkable tasks to *worker models* behind an OpenAI-compatible API (llama.cpp, Ollama, LM Studio,
vLLM, hosted APIs). The server runs the worker inside a sandbox, verifies the result with checks
the orchestrator declares, corrects it once when it can, and reports back a structured result with
evidence and metrics. It records every run and your feedback; using that to improve routing and
prompts is planned.

## Goals, stated so they can be measured

"Perfect" and "best in the world" are not testable. These are the targets, and the table says
honestly where each one stands today (see also [how deputy improves](docs/self-improvement.md)):

| Goal | Where it stands |
| --- | --- |
| Every claim a worker makes is checked by code, not trusted | **today**, for the claims you declare checks for (`contains`, `cites_lines`, `excludes`, `max_chars`, files, allow-listed commands) |
| Line citations correct on the benchmark; no writes outside the sandbox | citations are **measured** on a small built-in suite (a larger one is [#6](https://github.com/tommasomarchionni/deputy/issues/6)); the sandbox is covered by tests and a [written review](docs/security.md) |
| A worker never gets a shell | **today**: it has `read_file`, `write_file`, `grep`; checks run in the server from an allow-list (a `command` check still runs worker-written code with your rights: [#49](https://github.com/tommasomarchionni/deputy/issues/49)) |
| The orchestrator spends fewer tokens with deputy than without | **estimated today** on every run: what the worker read minus what the orchestrator paid for the request and the result (can be negative); the yardstick is an estimate (bytes / 4), documented in [configuration](docs/configuration.md#how-the-saving-is-estimated) |
| A task class that does not save tokens is not delegated | **advised today**: after 5 runs a flag (`not_worth_delegating`) and a line in `status` tell the orchestrator to do that kind of task itself; the server does not refuse |
| The router picks the cheapest path that passes the checks | planned ([#18](https://github.com/tommasomarchionni/deputy/issues/18)) |
| A change ships only if it beats the baseline, variance accounted for | **today** for request settings (`deputy tune`, one pass or `--runs N`); a held-out split and prompt or profile changes are planned ([#6](https://github.com/tommasomarchionni/deputy/issues/6), [#21](https://github.com/tommasomarchionni/deputy/issues/21)) |

## Documentation

Full site: **<https://tommasomarchionni.github.io/deputy/>** (source in `docs/`).

| Topic | Page |
| --- | --- |
| Install, connect Claude Code, Copilot, Cursor, Codex | [install](docs/install.md) |
| Configuration, tuning, updates | [configuration](docs/configuration.md) |
| The five tools and the checks | [tools](docs/tools.md) |
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
