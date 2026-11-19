# Tools

Six tools, short on purpose: the orchestrator pays for their schemas once per session. The whole
tool list is about 1143 770 tokens (bytes / 4, compact JSON); the part a model sees (names, descriptions,
arguments) about . A test keeps the list under 5,000 characters, about 1,250 tokens.

## `digest`

Ask a question about files instead of reading them. deputy writes the worker's task and its checks,
so this is the shortest way to delegate.

| Argument | Meaning |
| --- | --- |
| `files` | One to eight text files inside `workdir`. |
| `question` | What you want to know. |
| `workdir` | Absolute path the worker is confined to. |

Every line of the answer must carry its source, in the form `- the fact (docs/a.md:12 "exact words")`.
The status is `ok` only when each line states a fact, each citation names one of the files and a
real line of it, the quoted words are on that line, no line stands without a source, and the answer
is at most 2,000 characters.
Anything else, including an answer the files do not contain, comes back as `checks_failed`: read
the files yourself. Missing, empty, binary, secret-looking or outside paths are refused before any
model is called. The result has the same fields as `delegate`.

**What `ok` proves.** The sources are real: the quoted words are on the cited lines. It does not
prove the worker understood them, so a fact can be misread with a true citation. Read the cited line
before you act on a fact; that costs one line instead of the whole file.

## `delegate`

Run a task on a worker model and verify the result. Use it when you need your own checks, or work
other than answering a question about files.

| Argument | Meaning |
| --- | --- |
| `task` | What to do. Name files, do not paste them. |
| `workdir` | Absolute path the worker is confined to. |
| `profile` | `helper` by default; see `profiles`. |
| `checks` | Proofs the server runs on the answer (below). |
| `backend` | A configured backend; default is `default_backend`. |
| `max_turns` | Safety limit on the worker's tool turns (default 8). |
| `wait` | `true` by default. With `false` it returns at once with `status: running`; get the result with `collect`. |
| `agent` | `false` by default. With `true`, a local agent does a code change in a private copy (below). |

Returns: `run_id`, `status` (`ok`, `checks_failed`, `turn_limit`, `error`, and for background runs
`running`, `cancelled`), `answer`, a one-line `checks` summary (`3/3 passed`, or what failed with its
detail), and, only when there are any, `files_written` and `flags` (`needs_frontier`, `turn_limit`,
`backend_error`, `setup_error`, `time_limit`, `not_worth_delegating`, `small_input`).

The result is short on purpose. Metrics (turns, tokens, seconds, model) and the saving estimate
(`orchestrator_tokens_saved_est`, a rough bytes / 4 estimate of what you did not have to read) are
recorded in the trace and shown by `deputy stats`, not sent: a typical passing result is about 16 %
smaller (558 to 471 tokens in a measured case), and the tool's output schema is smaller too.
The worker always answers in English (the bundled profile says so), unless asked to translate.

### A local agent for a code change (`agent=true`)

With `agent=true`, `delegate` hands the whole task to Claude Code's own agent loop, running on your
local model through a small gateway inside deputy. The agent searches, edits and runs tests like a
Claude Code subagent, but no request reaches a hosted model.

- **It never touches your repository.** It works in a private copy (a git worktree of your current
  state, including uncommitted changes to tracked files; new untracked files are not copied), and
  what comes back is a patch:
  `Review, then apply: git apply <path>`. Generated files (`__pycache__`, `.venv`, `node_modules`)
  are left out of the patch.
- **It runs in the background.** The call returns `status: running` at once; get the result with
  `collect`. A run may take up to 3 hours: a local model is much slower than a hosted one.
- **Checks run on the copy**, so `file_contains` and allow-listed `command` checks see the agent's
  work. A failing check, or an agent that does not finish, comes back with `needs_frontier`.
- **What it needs:** the `claude` command, a git repository, and a local backend whose context
  holds at least 110,000 tokens (llama.cpp: `--ctx-size 131072`). deputy reads the size from a
  llama.cpp server and refuses a smaller one; with other servers (Ollama sets a small context by
  default) make sure of it yourself. Patches older than a week are removed.
- **What it cannot do** is in [security](security.md#the-local-agent).

## `collect`

The result of a `wait=False` delegation, by its `run_id` (the same id the result and `feedback` use).
It waits up to 20 seconds, so polling costs few tokens: if the job is still going you get
`status: running` and call it again. `cancel=true` stops the worker (the request to the model
server is dropped and a running command check is killed) and puts back every file the worker
wrote, so the workspace is as it was. A result can be collected once. At most 3 jobs run at once.

Jobs live in the memory of the running server: if the server exits, they are gone.

### Checks

| `type` | Passes when |
| --- | --- |
| `contains` | every string in `values` appears in the answer as a whole, case-insensitive: `12` is not found in `12,599` or `112`, `log` not in `logs` |
| `excludes` | none of the strings in `values` appears in the answer (for example a secret) |
| `file_contains` | the file `path` in `workdir` contains every string in `values` |
| `cites_text` | every quoted passage next to a line number (`line 2: "the exact words"`, `2: "..."`, `a.txt:2 "..."`, or `"..." (line 2)`) is on that line (or in that range) of the file `path`; case and spacing do not matter; at least one quoted citation is required |
| `cites_lines` | every cited line number (`line 12`, `lines 12-14`, `righe 12`, `l. 12`) is within `min`..`max`, and at least one is cited. Give `path` instead of `max` and the bound is that file's current number of lines, so the check stays right when the file grows |
| `json_schema` | the answer (or the file `path`) is JSON that validates against `json_schema` (JSON Schema, draft 2020-12); a code fence around the JSON is fine |
| `max_chars` | the answer is at most `limit` characters |
| `file_written` | the worker wrote `path` |
| `command` | an allow-listed `argv` exits 0 in `workdir` (see [configuration](configuration.md)) |

A failing check gets one correction round with the exact failure. If it still fails, the status is
`checks_failed` with `needs_frontier`: do the task yourself. The `answer` of a failed run is cut to
its first 300 characters (with a note), because you will redo the work and should not pay to read a
wrong answer; the failing `checks` keep their full detail.

## `status`

Configured backends and whether they answer.

## `profiles`

Available profiles, name and description.

## `feedback`

`run_id`, `verdict` (`accepted` or `rejected`) and an optional `reason`. Recorded in the trace;
this is what the tuning loop learns from. Send it for every result you used. A run that was
handed back (checks failed, out of turns or time) records a `rejected` verdict by itself, with the
reason; a verdict you send later wins. The `status` line says how many runs were judged.

### `not_worth_delegating`

deputy keeps count of what each *kind* of task has done for you (the profile plus the kinds of checks
asked for; benchmark runs do not count). Once a kind has been tried at least 5 times, it is flagged
when its mean estimated token saving is zero or negative, or when more than half of its runs were
handed back to the orchestrator. The flag appears in the `delegate` result and, with the numbers, in
the `status` tool (`advice`). The run itself is not refused: the flag tells the orchestrator to do
this kind of task itself from now on. The delegation guide says so.

### `small_input`

When every existing file a task names is about a thousand tokens or less (bytes / 4), the result
carries `small_input` with the estimate: reading such a file costs the orchestrator about what the
request and the result cost, so next time read it yourself. This needs no history, unlike
`not_worth_delegating`. It never refuses a run, and a task that names no file gets no flag.

### Checking what a citation says, not only where it points

`cites_lines` only proves that the cited numbers are plausible. A worker can cite a real line that says
something else. `cites_text` closes that gap: it finds every *quoted passage* (at least 8 characters,
in double quotes, typographic quotes or backticks, or single quotes) that sits right next to a line
reference and checks that the passage is on that line. A quote with no reference next to it is not
a citation; one wrong quote fails the check and the detail names the line and the passage. Use both:

```json
{"checks": [
  {"type": "cites_lines", "min": 1, "max": 400},
  {"type": "cites_text", "path": "docs/policy.md"}
]}
```

and ask the worker, in the task, to quote the exact words of every line it cites. The check is
about one file; run one per file.

## Listing files: `list_files`

`list_files(path, pattern)` returns the files under a folder with their sizes, leaving out what git
ignores, node_modules and secret-looking files. `pattern` is a glob matched against the file name or
its path from `path` (`*.md`, `adr/ADR-*.md`). Up to 200 files are listed one per line; more are
grouped by folder with a count, so the worker narrows the path instead of reading a long list.

## History: read-only `git`

The worker can read the history of a git repository: `git` with args such as
`["log", "--oneline", "-n", "20"]`, `["diff", "--stat", "HEAD~3"]`, `["show", "abc123", "--", "file"]`
or `["blame", "-L", "10,20", "--", "file"]`. Only `log`, `show`, `diff` and `blame`, with an
allow-list of options; paths go after `--`. The output is capped at 60,000 characters and says when
it was cut, and it counts as read on your behalf. Questions such as "what changed in this range and
why" or "which commit changed this rule" are long to read and short to answer: good delegations.
What is refused, and why, is in [security](security.md).

## Recipe: why did CI fail?

A failed CI run is one of the best things to delegate: the log is long and disposable, the answer
is short and checkable. The worker has no network and no credentials, so the orchestrator fetches
the log into a file (it pays only for the command, not for the output) and delegates the question:

```sh
gh run view <run-id> --log-failed > ci.log     # or --log for the whole run
```

```json
{"task": "ci.log is the log of a failed CI run. Which job failed, which test or step, and why?",
 "workdir": "/abs/path/to/repo",
 "checks": [{"type": "max_chars", "limit": 1200}]}
```

Add a `contains` check for anything you can predict (the job name, say). On the benchmark's
1,000-line CI log (about 18,000 tokens) the local 27B worker named the job, the test and the
assertion in 3 runs of 3, in about 2 minutes, with `summarise_log` and `grep`. On a real failed run
it also explained that a second job failed only because the first did.

If one search finds the answer (`grep -n "##\[error\]" ci.log`), run it yourself: a few hundred
tokens and seconds. Delegate when the cause needs reading around the error.

## Large logs: `summarise_log`

For a big repetitive file (a log, a test run) the worker has a tool that needs no model at all:
`summarise_log(path)` reduces every line to a template (digit runs become `#1`, `#2`, ...) and
reports each distinct kind of line once: how many lines it stands for, their first and last line
number, a real example, and the values of the numbers that vary little (a worker id, a status).
Lines that occur at most three times are shown whole with their own line number, so a unique error
is never folded away. Only repetition is removed; no kind of line is hidden. A 2,400-line log of
34,500 tokens becomes about 120 tokens, in milliseconds. The worker is told to try it before
`scan_file`, and to read the exact lines it points to.

This is the opposite of a summariser that keeps only lines with a log level: measured on a real CI
run of 215 lines, such a filter kept 54 tokens of 7,700 and called the log nearly empty, while
`summarise_log` kept all 176 distinct kinds of line in 3,000 tokens.

## Large files: `scan_file`

A model has a limited context, so a very large file cannot be read in one pass. The worker has a tool
for it, `scan_file(path, question)`: the server cuts the file into parts of whole lines (about 24,000
characters each), asks the worker model the question about each part in a fresh conversation, and
returns the findings of all parts, each under its range of lines. **Every line keeps its number in
the original file**, so what the worker cites at the end is true of the whole file, and `cites_lines`
and `cites_text` check it as usual.

Costs, plainly: a scan runs one model call per part (at most 12, so files up to about 290 KB), one
after the other, and the whole file is processed. On a local model that is slow at reading its
prompt (a 27B model on a laptop reads about 50 tokens a second) 70 KB took about 8 minutes, so a scan
has a 10 minute budget and says where it stopped. The worker is told to try `grep` first, which is
fast, and to use `scan_file` only when grep cannot answer. The orchestrator pays nothing for the
parts: the whole file counts as read on its behalf in `orchestrator_tokens_saved_est`.

## Searching: `grep`

A `grep` with up to 50 hits returns them as `file:line: text`. With more, it returns every matching
file once, with its number of hits and its first line numbers, so no file is hidden past a cap (one
real search had 66 hits in 29 files: the list costs about 490 tokens, and the worker then greps the
file it needs).
In a git work tree, `grep` skips what git ignores (generated folders, mirrored worktrees) and still
finds untracked files; elsewhere it searches everything. This only narrows a search: the sandbox's
own refusals still apply to each file.
For the saving estimate a search counts only the hit lines it returns, because that is all an orchestrator
searching by itself would read; counting each matching file would inflate the figure (one search of a
large repository once reported 34 million tokens).

### Structured answers: `json_schema`

For a list, a table or any structured answer, ask for JSON and check it against a schema instead of
reading it:

```json
{"type": "json_schema", "json_schema": {
  "type": "object", "required": ["rules"],
  "properties": {"rules": {"type": "array", "minItems": 2,
    "items": {"type": "object", "required": ["line", "text"]}}}}}
```

The answer may be wrapped in a code fence. Add `path` to validate a file the worker wrote instead.
On a mismatch the worker is told exactly what is wrong, with the place inside the document
(`$.rules[1].line: 'x' is not of type 'integer'`, `$.rules[0]: 'text' is a required property`; the
first five errors, and how many more). A schema that is not valid JSON Schema is refused up front
(`invalid check`). A `$ref` to a URL is never fetched: it fails with "unavailable", so the check can
never make the server reach out to the network.
