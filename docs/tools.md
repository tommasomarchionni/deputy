# Tools

Four tools, short on purpose: the orchestrator pays for their schemas on every turn. A test keeps
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

Returns: `status` (`ok`, `checks_failed`, `turn_limit`, `error`), `answer`, `files_written`,
`checks` (pass or fail each), `flags` (`needs_frontier`, `turn_limit`, `backend_error`,
`setup_error`, `not_worth_delegating`), `metrics` (turns, tokens, seconds, model), `run_id`, and
`orchestrator_tokens_saved_est` (a rough chars/4 estimate of what you did not have to read).
The worker always answers in English (the bundled profile says so), unless asked to translate.

### Checks

| `type` | Passes when |
| --- | --- |
| `contains` | every string in `values` appears in the answer (case-insensitive) |
| `excludes` | none of the strings in `values` appears in the answer (for example a secret) |
| `file_contains` | the file `path` in `workdir` contains every string in `values` |
| `cites_lines` | every cited line number (`line 12`, `lines 12-14`, `righe 12`, `l. 12`) is within `min`..`max`, and at least one is cited |
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
