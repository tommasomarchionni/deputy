# deputy

> Your frontier model plans and reviews. A cheaper model does the long reading and the code
> changes that tests can check. Code checks every result.

[![CI](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml/badge.svg)](https://github.com/tommasomarchionni/deputy/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-github%20pages-blue.svg)](https://tommasomarchionni.github.io/deputy/)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/tommasomarchionni/deputy/badge)](https://scorecard.dev/viewer/?uri=github.com/tommasomarchionni/deputy)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/16896/badge)](https://www.bestpractices.dev/projects/16896)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

deputy is an MCP server that lets your coding agent (Claude Code, or any MCP client) keep its
tokens for planning and review. It takes two kinds of work off the frontier model:

- **Code changes that tests or linters can check.** A local agent makes them on your own model
  (llama.cpp), in a private copy of the repository, one change per short conversation, until the
  project's checks pass. Your agent gets a patch to review and apply.
- **Long reading.** "Summarise this 900-line document", "what changed in the last ten commits, and
  why": a cheaper model (local, or any OpenAI-compatible API) reads, code proves every quoted line,
  and your agent reads a short answer, not the files.

**Status: alpha, in daily use.** It works end to end, is tested and measured on real repositories,
and is used to develop a real multi-app project. Expect breaking changes. Verified with Claude Code;
GitHub Copilot, Cursor and Codex are configured by the installer but not yet verified. The local
agent needs Claude Code.

## When it pays, and when it does not

| Hand over | Do it yourself |
| --- | --- |
| Code changes you can state and check: a list of small changes with their tests, removals, renames, mechanical edits across files | A change of a few lines you are waiting for: a local model takes minutes where a hosted one takes seconds |
| Long reads: summaries, inventories, cross-references, facts with their lines | A change that needs analysis nobody has done yet: on a real issue the local model did the mechanical steps and not the hard one |
| Big logs and test output (`summarise_log`) | Lookups in a short file or one line (deputy flags these as `small_input`), and code you are about to edit |
| Questions about git history (`git log`, `diff`, `show`, `blame`) | Searches that only list files: you would see the same list |

What was measured, with its limits, is on the [evidence](docs/evidence.md) page. In short:

- **Code changes.** Eight small changes to this repository (code, tests and documentation each):
  Claude Sonnet 5.5 alone spent about 137,000 weighted tokens in 2.5 minutes; handing them to
  Qwen3.8 27B in eight steps it spent 77,660, about 43 % fewer, and waited 88 minutes. Both
  results passed the tests, the linter, the formatter and the type checker. One run, on a laptop.
- **Reading.** On 21 everyday tasks on two real repositories, run twice with a local 27B worker,
  every answer was correct, confirmed by a frontier model reading the files. Reading tasks saved
  about 4,800 to 5,600 frontier tokens each; lookups and searches saved about nothing.
- **Where your tokens really go.** On one real project the large reads were at most 7 % of what
  the sessions spent, the subagents that edit code 43 %, and reading again a context above
  200,000 tokens 38 %. `deputy discover` prints the same table for your own sessions, so you know
  which of the three is worth your time.

## Quick start

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init                               # point it at your model server
deputy doctor                             # check that it answers, and what the local agent can use
deputy install claude-code --apply        # once: the MCP server, the skill and the hints
```

For Claude Code that one command does everything, for every project: it registers the MCP server,
installs the skill that teaches the agent when to delegate (`~/.claude/skills/deputy/`, loaded only
when relevant, kept current by deputy on each update), and adds two hints: one line before a large
read, and a few lines with each request that send checkable code changes to the local agent, so
you do not have to ask for it (`deputy hook off` turns both off; `code_changes = "frontier"` in the
[configuration](docs/configuration.md#code-changes) leaves code changes to your agent's choice).
Codex: `deputy install codex --apply` keeps its guide current too. Copilot and Cursor read the
guide from files in the project: `deputy install vscode|cursor --apply`, then
`deputy instructions --for copilot|cursor --apply` in each project (`deputy doctor` says when a
copy needs refreshing).

The local agent needs the `claude` command, a git repository and a llama.cpp server whose context
holds at least 110,000 tokens (`--ctx-size 131072`); what else to set on the server is in
[install](docs/install.md).

deputy keeps itself current: a newer compatible release is installed when the server starts, only
after its build provenance is verified, never a new major version or a release marked as breaking
(`auto_update` in the [configuration](docs/configuration.md#updates); `notify` or `off` to opt out).
A session that is already open keeps the version it started with until you reconnect the server
(in Claude Code: `/mcp`).

## How it works

**A code change** (`delegate` with `agent=true`):

1. Your agent writes what the changes share in `task`, one change per item of `steps`, and the
   project's own test and lint commands as checks. It does not read the code first.
2. deputy copies the repository (a git worktree, with your uncommitted changes to tracked files)
   and runs Claude Code's own agent loop on your local model, confined to that copy: its file
   tools cannot leave it, and its shell has no network, cannot read your keys and writes only
   there. Each step gets a conversation of its own.
3. The checks run on the copy. Failures go back to the local agent, up to three times. A step it
   cannot do is undone, its draft is kept apart, and the answer names it.
4. Your agent waits with `deputy wait <run_id>` (one turn, no polling), reads the patch, applies
   it with `git apply`, and does only what was left.

**A question about files** (`digest`, or `delegate` with your own checks):

1. Your agent names the files and the question; deputy adds the checks (every quoted line must be
   in that file at that line), or you declare them: contain these facts, stay under 1,500
   characters, pass this command.
2. The worker reads inside that folder only. It has no shell, no network and cannot read
   secret-looking files. Its tools: `read_file`, `write_file`, `grep`, `list_files`, read-only
   `git`, `summarise_log` (a model-free summary of a big log) and `scan_file` (a file larger than
   the model's context, read in parts).
3. The server runs the checks. A failing answer gets one correction round; if it still fails, the
   result says `needs_frontier` and your agent does the task itself.

Either way the result is short: `status`, `answer`, a one-line check summary, and files or flags
only when there are any.

## Commands

| Command | What it does |
| --- | --- |
| `deputy init`, `deputy doctor` | write a starter configuration; check it, the backends and what the local agent can use |
| `deputy install`, `deputy instructions` | connect an agent; print or write the delegation guide |
| `deputy hook on\|off\|status` | the Claude Code hints |
| `deputy wait RUN_ID` | wait for a local agent's run and print its result |
| `deputy discover` | where your agent's reading and tokens went in past Claude Code sessions, and how much a delegation could take |
| `deputy stats` | what deputy did for you: runs, pass rate, tokens saved, per kind of task |
| `deputy bench` | your model on the built-in suite, or your own tasks with `--tasks-file`; `--compare A.json B.json` compares two runs |
| `deputy tune` | measure, then recommend or apply request settings that measure better |
| `deputy update` | check for a newer release and upgrade |

## Documentation

Full site: **<https://tommasomarchionni.github.io/deputy/>** (source in `docs/`).

| Topic | Page |
| --- | --- |
| Install and connect an agent | [install](docs/install.md) |
| Configuration, backends, routing, updates | [configuration](docs/configuration.md) |
| The tools, the local agent, the checks and the flags | [tools](docs/tools.md) |
| Security model | [security](docs/security.md) |
| Measured results | [evidence](docs/evidence.md) |
| Benchmark, tuning and where your tokens go | [self-improvement](docs/self-improvement.md) |
| Design, state, local model speed | [architecture](docs/architecture.md), [memory](docs/memory.md), [local serving](docs/local-serving.md) |
| Roadmap | [roadmap](docs/roadmap.md) and the [project board](https://github.com/users/tommasomarchionni/projects/3) |

## Works well with rtk

[rtk](https://github.com/rtk-ai/rtk) shortens the output of shell commands your agent runs (git,
test runners); deputy takes long reads and checkable code changes off your agent. They do not
overlap, and measured numbers for both are on the [evidence](docs/evidence.md) page.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report
security issues privately: [SECURITY.md](SECURITY.md).

## License

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
