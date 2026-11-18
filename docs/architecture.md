# Architecture

!!! note "What exists today"
    Built and tested: the four tools, the execution loop with sandbox, checks and one correction round,
    OpenAI-compatible backends, the benchmark and tuning commands, workspaces, install and update.
    Planned: asynchronous tasks, routing by measured profile, memory, a second adapter family
    (see the [roadmap](roadmap.md)). Sections below that describe planned work say so.

## Roles

- **Orchestrator** (frontier model, in an agent such as Claude Code or Copilot): plans, splits the
  work, decides what to delegate, checks the result, owns every decision that touches the real
  repository.
- **deputy** (this MCP server): runs tasks on workers, enforces the sandbox, verifies, retries
  within a budget, records measurements.
- **Worker** (any model behind an adapter): does a bounded task. It never decides scope and never
  has a shell.

## Tools exposed over MCP (small, named for the job)

| Tool | Purpose |
| --- | --- |
| `deputy_delegate` | Run one task. Inputs: task, profile, sandbox or workdir, budget (time, tokens), `checks`. Output: `status`, `answer`, `artifacts`, `evidence`, `metrics`, `flags`. |
| `deputy_submit` / `deputy_poll` / `deputy_cancel` | The same work, asynchronously (MCP tasks), for slow local models. |
| `deputy_status` | Backends, loaded models, health, queue. |
| `deputy_profiles` | Profiles and model profiles known to the server. |
| `deputy_feedback` | The orchestrator says accepted / rejected and why; this feeds memory and the benchmark. |
| `deputy_memory_*` | Read and propose memory entries (see `memory.md`). Writes are reviewed. |

Results are structured (JSON schema), not free text: the orchestrator can act on `flags` such as
`needs_frontier`, `checks_failed`, `truncated`, `low_confidence`.

## Execution loop (in the server, not in the model)

1. **Plan the call:** classify the task, pick a model and parameters from measured model profiles,
   budget the context (chunk or map-reduce a file that does not fit).
2. **Run the worker** with only `read_file` and `write_file`, confined to the sandbox (real-path
   check, no symlink escape), no network except to the model endpoint.
3. **Verify with `checks`:** declared by the orchestrator, run by the server from an allow-list
   (for example: every quoted line number contains the quoted text; `node --test <file>` in the
   sandbox; a JSON schema; a required-facts list). The worker never runs commands.
4. **Correct:** on failure, one or two retries with the precise failure, within the budget.
5. **Escalate:** still failing: return `needs_frontier` with the evidence so the orchestrator takes
   over. (Planned: low-confidence detection, and delegating again from inside a run only for
   splitting a task, depth <= 2, inside the same budget.)
6. **Record:** trace and metrics, always; memory only through the reviewed path.

## Routing (planned)

By task class, size, latency and cost budget, using **measured** model profiles (tokens/s, context
that really works, tool-call reliability, best reasoning budget per class), with deterministic
fallback. Local first when the checks pass; hosted only when allowed by the project policy.

## Security

- Treat file contents and model output as data, never as instructions to the server.
- Sandbox confinement, write only inside it; the real repository is changed by the orchestrator
  after review, never by a worker.
- Redact secrets before a prompt leaves the process; never log a key; keys come from files with
  mode 600 or the OS keychain, not from the command line.
- Per-project policy file: allowed paths, allowed checks, allowed backends, budgets.
- Audit log of every run (who asked, which model, which files, which checks, outcome).

## Stack (choices to confirm with measurements, not dogma)

- Python 3.12+, `uv`; the official MCP SDK / FastMCP; pydantic v2; httpx.
- Transports: stdio first (works in Claude Code and VS Code/Copilot), streamable HTTP later.
- Adapters behind one interface: llama.cpp server, Ollama, OpenAI-compatible, Anthropic-compatible.
- SQLite (WAL, FTS5) for memory, traces and benchmark results; `sqlite-vec` only if the benchmark
  shows retrieval by meaning beats keyword search.
- OpenTelemetry-shaped JSONL traces.
- Quality: `ruff`, `pyright --strict`, `pytest` with `hypothesis` for the sandbox and parsers,
  contract tests shared by every adapter, CI on GitHub Actions with actions pinned to a full SHA.

## Token economy of the orchestrator (the reason this exists)

Delegation only makes sense if the frontier model spends **fewer** tokens than doing the work
itself. Its cost per delegation is: the tool schemas it carries on every turn, the task text it
writes, and the result it reads. So:

- **Few, short tool schemas.** Six tools, one-line descriptions, no prose manual. Tool definitions
  are paid on every turn of every conversation, delegated or not. (Measured on the tool this project grew from: 16 tools, 22,050 characters of definitions,
  about 5,500 tokens by a rough chars/4 estimate, in Spanish and verbose. Target here: under 1,500.)
- **Results by reference.** The result carries a compact `answer` (a few hundred tokens at most),
  `artifacts` as sandbox paths, and `evidence` as pass/fail per check, not the content. The
  orchestrator reads an artifact only if it decides to.
- **Checks replace re-reading.** If the server ran the declared checks and they passed, the
  orchestrator does not reread the file to confirm. A `verified: true` is worth more than a summary.
- **Task text by reference.** The task names files and a profile; it does not paste them. Profiles
  carry the standing rules, so they are not repeated in every call.
- **Delegate input-heavy, output-light work:** read a 12k-token file and return 15 lines. Do not
  delegate what the orchestrator can answer in one short turn; the benchmark finds the break-even.
- **Measure it.** Every run records `orchestrator_tokens` (task + result + schema share) and a
  `baseline_tokens` estimate of doing the work in the orchestrator. The benchmark reports
  `tokens_saved` per task class, and a class with no saving is routed away from delegation.

## Cross-agent use

An agent integration is one MCP config plus one short instruction file (for Copilot:
`.github/instructions/*.md`; for Claude Code: a skill). The protocol is the same, so a benchmark
run through the Copilot path must give the same results as through Claude Code.
