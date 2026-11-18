# Architecture

## Roles

- **Orchestrator**: the frontier model in your agent (Claude Code or another MCP client). It plans,
  decides what to delegate, declares the checks, and owns every change to the real repository.
- **deputy**: this MCP server. It runs the task on a worker, keeps the worker inside a sandbox,
  runs the checks, gives one correction round, and records what happened.
- **Worker**: any model behind an OpenAI-compatible API. It does one bounded task. It never has a
  shell, the network or credentials.

## Tools exposed over MCP

| Tool | Purpose |
| --- | --- |
| `delegate` | Run one task: `task`, `workdir`, `profile`, `checks`, optional `backend`, `max_turns`, and `wait` (false returns at once). Returns `run_id`, `status`, `answer`, a one-line `checks` summary, and `files_written` and `flags` when there are any. |
| `collect` | The result of a background `delegate`, waiting up to 20 s; `cancel` stops it and puts written files back. |
| `status` | The backends and whether they answer, the measured model profile, a week of usage, and advice on kinds of task that do not pay. |
| `profiles` | The worker profiles available for a folder. |
| `feedback` | The orchestrator says `accepted` or `rejected`, and why; recorded in the trace. |

Five tools with short descriptions: their schemas cost the orchestrator about 970 tokens per
session. Details of each field, check and flag: [tools](tools.md).

## What happens in one delegation

1. **Prepare:** load the profile, validate the checks, choose a backend (see Routing), and add the
   project memory and any lessons that apply to this kind of task.
2. **Run the worker** with its tools, all confined to `workdir`: `read_file`, `write_file`, `grep`,
   read-only `git`, `summarise_log` and `scan_file`. The turn limit comes from the measured model
   profile unless the call or the profile sets one.
3. **Verify:** the server runs the declared checks on the answer and on written files. A `command`
   check runs only an allow-listed argv, without a shell.
4. **Correct:** if a check fails, the worker gets the exact failures and one more try (a profile
   can set none or more).
5. **Hand back:** still failing, the result says `checks_failed` with the flag `needs_frontier`,
   and the answer is cut to a short excerpt, because the orchestrator will redo the task.
6. **Record:** one line in the trace (`runs.jsonl`): metadata, check outcomes, metrics and the
   saving estimate; never file contents or answers.

## Routing

With several backends configured, `delegate` picks one by the rules in `router.py`: local before
hosted, measured to pass before not measured, faster measured time first, then the default, then
the configuration order. It never goes to a more expensive or less private kind than the default.
A backend that does not answer is skipped, a backend error moves on to the next, and the reason is
written to the trace (`route`).

## Where state lives

All on your machine, in the state directory (`~/.local/state/deputy`): the trace (`runs.jsonl`),
measured model profiles, lessons, project memory (keyed by repository, outside the repository),
optimiser proposals and nightly benchmark reports, as JSON files. Configuration is in
`~/.config/deputy/config.toml`; API keys come from a mode-600 file or an environment variable,
never from the configuration file or the command line.

## Token economy of the orchestrator

Delegation makes sense only if your frontier model spends fewer tokens than doing the work itself.
Its cost per delegation is the task text it writes and the result it reads; the tool schemas are a
fixed cost per session. So:

- **Short schemas** (about 970 tokens for all five tools) and a **short result** (the answer, a
  one-line check summary, nothing empty).
- **Checks replace re-reading:** if the checks passed, the orchestrator does not reread the files.
- **The task names files; it does not paste them.** Standing rules live in the profile.
- **Delegate input-heavy, output-light work.** Every run records what the worker read and what the
  orchestrator paid; `deputy stats` reports the net saving per kind of task, and a kind that does
  not pay is flagged `not_worth_delegating`. A task that names only small files is flagged
  `small_input` at once.

## Stack

- Python 3.11+, `uv`; the official MCP SDK; pydantic v2; httpx. Transport: stdio.
- One adapter family: the OpenAI-compatible chat API (llama.cpp, Ollama, LM Studio, vLLM, hosted
  APIs).
- Quality: `ruff`, `pyright --strict`, `pytest` with `hypothesis` and snapshot tests, CI on GitHub
  Actions with actions pinned to a full SHA, CodeQL, releases with provenance attestations.

## Cross-agent use

An agent integration is one MCP configuration (`deputy install`) plus one short instruction file
(`deputy instructions`). The protocol is the same for every agent; only Claude Code is verified in
its app so far.
