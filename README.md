# deputy

> Your frontier model plans. A cheaper model does the long reading. Code checks every answer.

[![CI](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml/badge.svg)](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-github%20pages-blue.svg)](https://tommasomarchionni.github.io/deputy/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

deputy is an MCP server. Your coding agent (Claude Code, or any MCP client) hands it a read-heavy
task, such as "summarise this 900-line document" or "which files mention this setting". deputy runs
the task on a cheaper model (a local llama.cpp or Ollama model, or any OpenAI-compatible API),
checks the answer with code, and returns a short result. Your agent reads the answer, not the files.

**Status: alpha.** It works end to end and is tested; expect breaking changes. Verified with
Claude Code; GitHub Copilot, Cursor and Codex are configured by the installer but not yet verified.

## When it pays, and when it does not

| Delegate | Do it yourself |
| --- | --- |
| Long reads: summaries, inventories, cross-references, searches across many files | Tiny lookups in a short file (deputy flags these as `small_input`) |
| Big logs and test output (`summarise_log`) | Reading code you are about to edit |
| Questions about git history (`git log`, `diff`, `show`, `blame`) | Anything that needs a shell, the network or credentials |

Measured on a real multi-app repository (6 read-heavy tasks, a local 27B worker, version 0.12):
about 6,900 frontier tokens saved per task on average, from about 0 on the smallest task to about
22,500 on a long summary. The checks passed on 5 of 6 tasks; the sixth was caught by its check and
handed back (a search limit, fixed since).
These are estimates (bytes / 4), and worker time is not free: minutes for long documents on a
laptop. Details and limits: [evidence](docs/evidence.md).

## Quick start

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init                               # point it at your model server
deputy doctor                             # check that it answers
deputy install claude-code --apply        # once: register the MCP server (or: vscode, cursor, codex, generic)
deputy instructions --for claude --apply  # in each project folder: a skill that tells the agent when to delegate
```

`install` gives your agent the tools; `instructions` teaches it when to use them. The Claude Code
skill is loaded only when relevant, so it costs no tokens otherwise.

## How it works

1. Your agent calls `delegate` with a task, a folder and the **checks** the answer must pass (for
   example: cite line numbers in range, contain these facts, stay under 1,500 characters).
2. The worker reads files inside that folder only. It has no shell, no network and cannot read
   secret-looking files.
3. The server runs the checks. A failing answer gets one correction round; if it still fails, the
   result says `needs_frontier` and your agent does the task itself.

The result is short: `status`, `answer`, a one-line check summary, and files or flags only when
there are any.

**What the worker can use:** `read_file`, `write_file`, `grep` (respects `.gitignore`; groups large
results by file), read-only `git` (`log`, `show`, `diff`, `blame`), `summarise_log` (a model-free
summary of a big log that drops only repetition) and `scan_file` (a file larger than the model's
context, read in parts).

## Measure it on your own work

| Command | What it tells you |
| --- | --- |
| `deputy discover` | where your agent's reading went in past Claude Code sessions, and how much a delegation could take |
| `deputy bench --tasks-file tasks.toml --workdir .` | your own tasks on your own repository: pass or fail, time, tokens saved |
| `deputy stats` | what deputy did for you: runs, pass rate, tokens saved, per kind of task |
| `deputy bench` | your model on the built-in suite; `--compare A.json B.json` compares two runs |

deputy also improves with use: `deputy tune` picks request settings that measure better,
`deputy learn` turns repeated failures into short lessons for the worker (kept only if they help),
and `deputy optimise` proposes a better worker profile that you approve. See
[how deputy improves](docs/self-improvement.md).

## Documentation

Full site: **<https://tommasomarchionni.github.io/deputy/>** (source in `docs/`).

| Topic | Page |
| --- | --- |
| Install and connect an agent | [install](docs/install.md) |
| Configuration, backends, routing, updates | [configuration](docs/configuration.md) |
| The tools, the checks and the flags | [tools](docs/tools.md) |
| Security model | [security](docs/security.md) |
| Measured results | [evidence](docs/evidence.md) |
| Benchmark, tuning, lessons, optimiser | [self-improvement](docs/self-improvement.md) |
| Design, memory, local model speed | [architecture](docs/architecture.md), [memory](docs/memory.md), [local serving](docs/local-serving.md) |
| Roadmap | [roadmap](docs/roadmap.md) and the [project board](https://github.com/users/tommasomarchionni/projects/3) |

## Works well with rtk

[rtk](https://github.com/rtk-ai/rtk) shortens the output of shell commands your agent runs (git,
test runners); deputy takes long reads off your agent. They do not overlap, and measured numbers
for both are on the [evidence](docs/evidence.md) page.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report
security issues privately: [SECURITY.md](SECURITY.md).

## License

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
