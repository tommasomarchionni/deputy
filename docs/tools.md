# Tools

Five tools, short on purpose: the orchestrator pays for their schemas on every turn. A test keeps
the total under about 1,500 tokens.

## `delegate`

Run a task on a worker model and verify the result.

| Argument | Meaning |
| --- | --- |
| `task` | What to do. Name files, do not paste them. |
| `workdir` | Absolute path the worker is confined to. |
| `profile` | `helper` by default; see `profiles`. |
| `checks` | Proofs the server runs on the answer (below). |
| `backend` | A configured backend; default is `default_backend`. |
| `max_turns` | Safety limit on the worker's tool turns (default 8). |
| `wait` | `true` by default. With `false` it returns at once with `status: running`; get the result with `collect`. |

Returns: `status` (`ok`, `checks_failed`, `turn_limit`, `error`, and for background runs `running`, `cancelled`), `answer`, `files_written`,
`checks` (pass or fail each), `flags` (`needs_frontier`, `turn_limit`, `backend_error`,
`setup_error`, `not_worth_delegating`), `metrics` (turns, tokens, seconds, model), `run_id`, and
`orchestrator_tokens_saved_est` (a rough chars/4 estimate of what you did not have to read).
The worker always answers in English (the bundled profile says so), unless asked to translate.

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
| `contains` | every string in `values` appears in the answer (case-insensitive) |
| `excludes` | none of the strings in `values` appears in the answer (for example a secret) |
| `file_contains` | the file `path` in `workdir` contains every string in `values` |
| `cites_text` | every quoted passage next to a line number (`line 2: "the exact words"`, `2: "..."`, `a.txt:2 "..."`, or `"..." (line 2)`) is on that line (or in that range) of the file `path`; case and spacing do not matter; at least one quoted citation is required |
| `cites_lines` | every cited line number (`line 12`, `lines 12-14`, `righe 12`, `l. 12`) is within `min`..`max`, and at least one is cited |
| `json_schema` | the answer (or the file `path`) is JSON that validates against `json_schema` (JSON Schema, draft 2020-12); a code fence around the JSON is fine |
| `max_chars` | the answer is at most `limit` characters |
| `file_written` | the worker wrote `path` |
| `command` | an allow-listed `argv` exits 0 in `workdir` (see [configuration](configuration.md)) |

A failing check gets one correction round with the exact failure. If it still fails, the status is
`checks_failed` with `needs_frontier`: do the task yourself.

## `status`

Configured backends and whether they answer.

## `profiles`

Available profiles, name and description.

## `feedback`

`run_id`, `verdict` (`accepted` or `rejected`) and an optional `reason`. Recorded in the trace;
this is what the tuning loop learns from.

### `not_worth_delegating`

deputy keeps count of what each *kind* of task has done for you (the profile plus the kinds of checks
asked for; benchmark runs do not count). Once a kind has been tried at least 5 times, it is flagged
when its mean estimated token saving is zero or negative, or when more than half of its runs were
handed back to the orchestrator. The flag appears in the `delegate` result and, with the numbers, in
the `status` tool (`advice`). The run itself is not refused: the flag tells the orchestrator to do
this kind of task itself from now on. The delegation guide says so.

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
