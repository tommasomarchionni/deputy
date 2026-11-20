# Architecture

## Roles

- **Orchestrator**: the frontier model in your agent (Claude Code or another MCP client). It plans,
  decides what to hand over, declares the checks, reviews what comes back and owns every change to
  the real repository.
- **deputy**: this MCP server. It runs the work on a cheaper model, keeps that model confined, runs
  the checks, lets the model correct itself a bounded number of times, and records what happened.
- **Worker**: any model behind an OpenAI-compatible API, for reading. It does one bounded task with
  file tools only. It never has a shell, the network or credentials.
- **Local agent**: Claude Code's own agent loop, driven by your local model through a gateway
  inside deputy, for code changes. It has a shell, so it works in a private copy of the repository
  and inside an OS sandbox ([security](security.md#the-local-agent)).

## Tools exposed over MCP

| Tool | Purpose |
| --- | --- |
| `digest` | Ask a question about named files; deputy writes the task and proves every citation. |
| `delegate` | Run one task with your own checks: `task`, `workdir`, `profile`, `checks`, optional `backend`, `max_turns` and `wait`. With `agent=true` and `steps`, hand a code change to the local agent and get a patch. |
| `collect` | The result of a background `delegate`, waiting up to 20 s; `cancel` stops it and puts written files back, or stops a local agent's run. |
| `status` | The backends and whether they answer, the measured model profile, a week of usage, and advice on kinds of task that do not pay. |
| `feedback` | The orchestrator says `accepted` or `rejected`, and why; recorded in the trace. |

Five tools with short descriptions: their schemas (bytes / 4) cost the orchestrator about 1,160
tokens per session. Details of each field, check and flag: [tools](tools.md).

## What happens in one delegation

1. **Prepare:** load the profile, validate the checks and choose a backend (see Routing).
2. **Run the worker** with its tools, all confined to `workdir`: `read_file`, `write_file`, `grep`,
   `list_files`, read-only `git`, `summarise_log` and `scan_file`. The turn limit comes from the
   measured model profile unless the call or the profile sets one.
3. **Verify:** the server runs the declared checks on the answer and on written files. A `command`
   check runs only an allow-listed argv, without a shell.
4. **Correct:** if a check fails, the worker gets the exact failures and one more try (a profile
   can set none or more).
5. **Hand back:** still failing, the result says `checks_failed` with the flag `needs_frontier`,
   and the answer is cut to a short excerpt, because the orchestrator will redo the task.
6. **Record:** one line in the trace (`runs.jsonl`): metadata, check outcomes, metrics and the
   saving estimate; never file contents or answers.

## What happens in one code change

1. **Copy:** deputy makes a git worktree of the repository as it is now, with the uncommitted
   changes to tracked files, and links the dependency folders (`node_modules`, `.venv`) into it.
2. **Start the agent:** `claude -p` runs in that copy with no settings, hooks or MCP servers of
   yours, without credentials, and talks only to deputy's gateway on `127.0.0.1`. The gateway
   turns each request into what a local model server accepts and keeps the prompt append-only,
   so the server reuses what it has already read.
3. **One step, one conversation:** each item of `steps` gets a conversation of its own in the same
   copy. A step has 30 minutes; an agent that makes 40 tool calls without changing a file is told
   to write the change with what it knows.
4. **Verify:** the checks (the project's tests, linter, formatter, type checker) run on the copy,
   in deputy's sandbox. Failures go back to the agent, up to three times; a round in which it
   changes no file is the last, because asking again cannot help.
5. **Undo what did not work:** a step the agent could not do is undone, so the patch holds only
   steps whose checks pass; what that step had changed is kept apart as a draft.
6. **Hand back:** a patch, a summary per step and the steps left for the orchestrator. The
   orchestrator waits with `deputy wait <run_id>`, one turn for the whole run, then reviews and
   applies the patch.

## Routing

With several backends configured, `delegate` picks one by the rules in `router.py`: local before
hosted, measured to pass before not measured, faster measured time first, then the default, then
the configuration order. It never goes to a more expensive or less private kind than the default.
A backend that does not answer is skipped, a backend error moves on to the next, and the reason is
written to the trace (`route`). The local agent always uses the local backend.

## Where state lives

All on your machine, in the state directory (`~/.local/state/deputy`): the trace (`runs.jsonl`),
the measured model profiles, and for each run of the local agent its patch, result and drafts
(removed after a week). Configuration is in `~/.config/deputy/config.toml`; API keys come from a
mode-600 file or an environment variable, never from the configuration file or the command line.

## Token economy of the orchestrator

Handing work over makes sense only if your frontier model spends fewer tokens than doing the work
itself. Its cost is the task text it writes and the result it reads; the tool schemas are a fixed
cost per session. So:

- **Short schemas** (about 1,160 tokens for the whole tool list, about 780 for what the model
  sees) and a **short result** (the answer, a one-line check summary, nothing empty).
- **Checks replace most re-reading:** if the checks passed, the orchestrator does not reread the
  files; before it acts on a fact, it reads that one cited line. For a code change it reads the
  patch, not the files.
- **The task names files; it does not paste them.** Standing rules live in the profile.
- **Hand over input-heavy, output-light reading, and code changes that tests can check.** Every
  run records what the cheaper model read and what the orchestrator paid; `deputy stats` reports
  the net saving per kind of task, and a kind that does not pay is flagged
  `not_worth_delegating`. A task that names only small files is flagged `small_input` at once.
- **Waiting costs one turn.** `deputy wait` blocks in a background shell until the run ends, so
  the orchestrator does not poll.
- **Know where the tokens go.** `deputy discover` splits what past sessions spent between main
  sessions, subagents that edit and subagents that read, and says how much of it a delegation
  could have taken ([self-improvement](self-improvement.md)).

## External tools

The worker's tools are pure Python inside the sandbox; the reading path requires no external
program. Decided on 2026-11-18 ([#111](https://github.com/tommasomarchionni/deputy/issues/111)):

- An external tool (ripgrep, ast-grep, a command-output compressor) may be added only as an optional
  accelerator: used when installed, with identical results, and only after a benchmark shows a gain.
  A missing tool never changes an answer.
- The worker never gets a shell, the network or credentials. Anything that needs them (for example
  `gh` to fetch a CI log) stays with the orchestrator, which saves the output to a file for the worker.
- [rtk](https://github.com/rtk-ai/rtk) compresses the shell output your agent reads; it complements
  deputy on the orchestrator's side and is documented, not wrapped
  ([evidence](evidence.md#where-an-agents-reading-goes-and-rtk)).
- `git` is the one program the worker can reach, read-only and through an allow-list
  ([security](security.md)).

The local agent is the exception, and an optional one: it needs the `claude` command and `git`,
and a sandbox tool of the system (`sandbox-exec` on macOS, bubblewrap on Linux). `deputy doctor`
says what is there.

## Stack

- Python 3.11+, `uv`; the official MCP SDK; pydantic v2; httpx. Transport: stdio.
- One adapter family: the OpenAI-compatible chat API (llama.cpp, Ollama, LM Studio, vLLM, hosted
  APIs). The gateway of the local agent speaks the Anthropic Messages API on one side and that
  API on the other.
- Quality: `ruff`, `pyright --strict`, `pytest` with `hypothesis` and snapshot tests, CI on GitHub
  Actions with actions pinned to a full SHA, CodeQL, an OpenSSF Scorecard, releases with provenance
  attestations.

## Cross-agent use

An agent integration is one MCP configuration (`deputy install`) plus one short instruction file
(`deputy instructions`). The protocol is the same for every agent; only Claude Code is verified in
its app so far, and the local agent needs Claude Code.
