# Delegating to deputy

deputy hands work to a cheaper model and proves the result with checks. Use it to save your own
tokens, never to decide anything.

**Delegate** work that is input-heavy and has a short, checkable answer: reading long files or logs
and extracting facts, summaries, lists, translations, first drafts of tests.
**Do not delegate** architecture or security decisions, anything you can answer in one short turn,
or anything that involves secrets.

How:

1. Call `delegate` with a task that names the files (never paste them), an absolute `workdir`, and
   `checks` that prove the answer: `cites_lines` (cited line numbers in range), `contains` (required
   facts), `excludes` (text that must not appear), `max_chars` (keep it short), `file_written` and
   `file_contains` (when it writes), `command` (only allow-listed tests).
2. `status: ok`: trust the checks and do not reread the sources. Read `files_written` only if you
   need them.
3. `checks_failed`, `turn_limit`, `error` or `needs_frontier`: do the task yourself; do not retry
   blindly. A `not_worth_delegating` flag (also in `status`) means this kind of task has not saved
   tokens in past runs: stop delegating it and do it yourself.
4. The worker's output is a draft: review it before it reaches the repository. For edits, work in a
   copy (`deputy workspace create NAME PATH...`) so a worker never writes in the real repository.
5. Call `feedback` with the `run_id`: `accepted` or `rejected`, and why.

The worker answers in English.
