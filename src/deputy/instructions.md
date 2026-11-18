# Delegating to deputy

deputy hands reading to a cheaper model and proves the answer with checks. Use it to save your own
tokens, never to decide anything.

**Delegate** when the task needs reading more than about 4 KB (a long file, several files, a big
log, git history) and the answer is short: summaries, inventories, cross-references, extracting
facts with their lines, log triage, "what changed and why". Decide **before** you read the input,
and do not read it first: reading it yourself is the cost you are avoiding.
**Do it yourself**: a lookup in one short file or one line, anything one search answers (you
would see the same lines), a file you are about to edit, decisions, anything involving secrets.

How:

1. Call `delegate` with an absolute `workdir`, and a task that names the files (never paste them).
   The worker has none of your context: say what to cover, in what form, and how long. Add `checks`
   that prove the answer: `cites_lines` with `path` (cited lines exist in that file), `contains` (facts you can predict),
   `excludes`, `max_chars`, `file_written` and `file_contains` (when it writes), `command` (only
   allow-listed tests).
2. `status: ok`: trust the checks and do not reread the sources. Before acting on one fact, you may
   check that single line.
3. `checks_failed`, `turn_limit`, `error` or `needs_frontier`: do the task yourself; do not retry
   blindly. `not_worth_delegating` (also in `status`) means this kind of task has not saved tokens:
   stop delegating it. `small_input` means the files were short: read such files yourself.
4. The worker's output is a draft: review it before it reaches the repository. For edits, work in a
   copy (`deputy workspace create NAME PATH...`).
5. Call `feedback` with the `run_id`: `accepted` or `rejected`, and why.

The worker answers in English.
